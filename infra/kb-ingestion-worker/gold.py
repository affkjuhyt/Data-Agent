"""
Gold stage — document-level promotion.

Pipeline (per document):
  1. Per chunk (NO LLM calls — silver already produced summary + intents):
     embed content → conflict detect → atomic commit (Postgres + Qdrant chunk
     collection). Chunk points carry data_id so retrieval can narrow by document.
  2. Map-reduce the chunk summaries (document order, batched, bounded depth)
     into ONE document summary.
  3. Extract entities + relationships ONCE from the document summary —
     Vietnamese-aware: names copied character-for-character with all diacritics,
     synonyms returned as aliases.
  4. Canonicalize entities (entity_registry: exact alias table first, then
     embedding match; learns Vietnamese synonym aliases as it goes).
  5. Commit the document graph to Neo4j (entities shared per KB; relationships
     keyed by data_id).
  6. Upsert the document-summary point into the doc collection LAST — a document
     absent from the doc collection is invisible to 2-step retrieval, so this
     write is the transaction boundary.
  7. Flip KBData tier to GOLD.

A failure in steps 2–6 leaves committed chunks but no doc point and no GOLD tier;
re-promotion first cleans up the previous partial state, so retry is safe.
"""
from __future__ import annotations

import json
import logging
import re
import time as _time
import unicodedata
import uuid
from collections import Counter
from typing import Any

from neo4j import AsyncDriver
from qdrant_client import models as qmodels
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import cast as sa_cast, func as sa_func, update as sa_update
from sqlalchemy.dialects.postgresql import JSONB

from basemodel.services_databaseconnector.postgres_model import (
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    KBConflictBatchInsert,
    KBConflictInsert,
    KBLifecycleHistoryInsert,
    Tier,
)
from sqlalchemy import select as sa_select
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
    KBQdrantCollectionORM,
    KBConflictBatchORM,
)
from basemodel.services_databaseconnector.qdrant_model import (
    SearchRequest,
)
from services.database_connector.model_connector import ModelServiceClient
from services.database_connector.mongo_connector import MongoClient
from services.database_connector.postgres_connector import PostgresClient
from services.database_connector.qdrant_connector import (
    vector_search as qdrant_vector_search,
)

from utils import config as cfg
from utils import entity_registry
from utils import llm_client
from utils.pipeline_events import PipelineEventEmitter

log = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJ   = re.compile(r"\{[\s\S]*\}", re.DOTALL)

# Words that are never meaningful knowledge-graph entities regardless of context.
# English + Vietnamese (documents are primarily Vietnamese). Compared against the
# NFC-casefolded entity name, so diacritics must be written exactly.
_GENERIC_BLACKLIST: frozenset[str] = frozenset({
    # English
    "model", "models", "system", "systems", "approach", "approaches",
    "method", "methods", "technique", "techniques", "architecture", "architectures",
    "pipeline", "pipelines", "module", "modules", "component", "components",
    "network", "networks", "framework", "frameworks", "process", "processes",
    "training", "evaluation", "inference", "generation", "optimization",
    "processing", "learning", "testing", "performance", "result", "results",
    "data", "task", "tasks", "problem", "problems", "solution", "solutions",
    "work", "paper", "papers", "document", "documents", "section",
    "table", "figure", "equation", "experiment", "experiments",
    "configuration", "setting", "settings", "mechanism", "mechanisms",
    "operation", "operations", "procedure", "procedures", "strategy",
    "strategies", "scheme", "schemes", "step", "steps", "stage", "stages",
    # Vietnamese — generic nouns that name no specific thing
    "hệ thống", "phương pháp", "mô hình", "quy trình", "tài liệu", "dữ liệu",
    "giải pháp", "thông tin", "nội dung", "kết quả", "vấn đề", "báo cáo",
    "bài viết", "chương", "phần", "mục", "bảng", "hình", "ví dụ", "trang",
    "tổ chức", "đơn vị", "cơ quan", "doanh nghiệp", "khách hàng", "người dùng",
    "sản phẩm", "dịch vụ", "ứng dụng", "nền tảng", "cấu trúc", "chức năng",
    "yêu cầu", "tính năng", "mục tiêu", "phạm vi", "đánh giá", "phân tích",
    "tổng quan", "giới thiệu", "kết luận", "công việc", "kế hoạch", "dự thảo",
})


def _fold(s: str) -> str:
    """NFC + casefold — the comparison form for blacklist / dedup checks.
    NFC matters for Vietnamese: composed vs decomposed diacritics must compare equal."""
    return unicodedata.normalize("NFC", s).casefold().strip()


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _json_hint(raw: str, exc: json.JSONDecodeError) -> str:
    msg = str(exc).lower()
    # An unterminated / truncated object almost always means the model emitted too many
    # items and ran out of tokens before closing the JSON — so the retry asks for FEWER.
    if "unterminated" in msg or (exc.pos and exc.pos >= len(raw) - 5):
        return (
            f"your response was cut off because it was too long — return FEWER items: "
            f"at most {cfg.MAX_DOC_ENTITIES} entities and {cfg.MAX_DOC_RELATIONSHIPS} "
            f"relationships, each with a description under 12 words, so the JSON closes properly"
        )
    if "expecting" in msg or "invalid" in msg:
        return f"syntax error near position {exc.pos} — return only a valid JSON object, no extra text"
    return "response is not valid JSON — return only the JSON object with no markdown or extra text"


async def _llm_generate_json(
    system_prompt: str,
    user: str,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    max_retries: int = 2,
    system_type: str = "",
) -> tuple[dict, str, int]:
    """Returns (parsed_dict, last_raw_response, attempts_used).

    `system_type` is a key of SYSTEM_PROMPTS and of utils.llm_schemas: it selects the
    JSON schema llm_client forwards to the model-service /generate gateway, which the
    served model applies as its response_format for constrained decoding.
    """
    current_user = user
    last_hint = ""

    for attempt in range(max_retries):
        raw = await llm_client.generate_json(
            current_user,
            system_prompt=system_prompt,
            system_type=system_type,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        log.debug("Gold LLM raw attempt %d (first 300): %r", attempt + 1, raw[:300])

        fence_m = _JSON_FENCE.search(raw)
        if fence_m:
            candidate = fence_m.group(1).strip()
        else:
            obj_m = _JSON_OBJ.search(raw)
            candidate = obj_m.group(0) if obj_m else raw.strip()

        candidate = candidate.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

        try:
            return json.loads(candidate), raw, attempt + 1
        except json.JSONDecodeError as exc:
            last_hint = _json_hint(raw, exc)
            log.warning("Gold LLM JSON invalid (attempt %d/%d): %s", attempt + 1, max_retries, last_hint)
            current_user = f"{user}\n\nNote: {last_hint}."

    raise ValueError(f"LLM failed to return valid JSON after {max_retries} attempts: {last_hint}")


# ── Prompts ───────────────────────────────────────────────────────────────────

_DOC_REDUCE_SYSTEM = """\
You combine partial summaries of ONE document into a single coherent summary.
Input: a numbered list of summaries, in document order. They may be in Vietnamese,
English, or mixed.

Output exactly this JSON, nothing else: {"summary":"..."}

RULES:
1. LANGUAGE — write in the SAME language as the input summaries. NEVER translate.
2. NAMES — copy every proper name (people, organisations, places, systems, laws,
   decrees, projects, products) CHARACTER-FOR-CHARACTER, keeping ALL Vietnamese
   diacritics and tone marks ("Đà Nẵng" stays "Đà Nẵng", never "Da Nang").
   Do NOT drop named things to save space: entity extraction runs on your output,
   and a name you omit is permanently lost from the knowledge graph. Prefer
   cutting generic phrasing over cutting names.
3. Keep the concrete numbers and dates that matter to the document.
4. At most 250 words. Plain text, no markdown, no list markers."""


_DOC_ENTITY_SYSTEM = f"""\
You extract knowledge-graph entities from a document summary. The document may be
in Vietnamese, English, or mixed.

Output exactly this JSON, nothing else:
{{"entities":[{{"name":"…","type":"…","aliases":["…"],"description":"…","score":0.0}}]}}
Return AT MOST {cfg.MAX_DOC_ENTITIES} entities, the most important first.

TYPE — choose the MOST SPECIFIC label that fits. Be decisive; do not default to
"concept" when a specific type applies:
  person         — named individuals ("Hồ Chí Minh", "Albert Einstein")
  organization   — companies, agencies, institutions ("NHNN", "Đại học Quốc gia Hà Nội")
  location       — countries, cities, regions, landmarks ("Đà Nẵng", "sông Mê Kông")
  law_document   — laws, decrees, circulars, standards ("Nghị định 13/2023/NĐ-CP")
  system_product — named systems, software, products, vehicles, works
  event          — wars, conferences, disasters, eras with a name ("Chiến tranh thế giới thứ hai")
  time_period    — named dynasties, eras, periods ("nhà Trần", "thời Pháp thuộc")
  concept        — ONLY when nothing above fits (theories, fields, abstract terms)

NAME RULES — critical for Vietnamese:
1. Copy "name" CHARACTER-FOR-CHARACTER from the text, keeping ALL diacritics and
   tone marks. Tone marks change meaning ("bán" = sell, "bàn" = table, "bạn" =
   friend) — stripping or altering them corrupts the entity. Never romanize.
2. NEVER translate a name. "Việt Nam" stays "Việt Nam" — output "Vietnam" only if
   the text itself wrote "Vietnam".
3. Use the fullest / most official form appearing in the text as "name". Put every
   OTHER surface form of the SAME entity into "aliases": abbreviations, acronyms,
   shortened forms, historical or colloquial names — whether they appear in the
   text or are widely used. Vietnamese refers to one entity by many names; the
   aliases list is how the knowledge base joins them. Examples:
     name "Thành phố Hồ Chí Minh" → aliases ["TP.HCM","TPHCM","Sài Gòn"]
     name "Ngân hàng Nhà nước Việt Nam" → aliases ["NHNN"]
     name "Hoa Kỳ" → aliases ["Mỹ"]
   Use [] when there are none.

INCLUDE — named, specific things: organisations / agencies / companies, people,
places, laws / decrees / circulars (e.g. "Nghị định 13/2023/NĐ-CP"), named systems
/ products / projects / standards, datasets, precise technical concepts.

EXCLUDE — generic nouns in ANY language that name no specific thing:
  ✗ Vietnamese: hệ thống, phương pháp, mô hình, quy trình, tài liệu, dữ liệu,
    giải pháp, thông tin, nội dung, kết quả, báo cáo, tổ chức, đơn vị, sản phẩm
  ✗ English: system, method, model, process, document, data, solution, report

SELF-CHECK per entity: "Could this be the title of a Wikipedia article or an
official record?" If no, exclude it.

"description": ≤ 12 words, in the SAME language as the document.
"score": 0.0–1.0 — importance of the entity to the whole document."""


_RELATIONSHIP_SYSTEM = f"""\
You build knowledge graph edges. Given a list of ENTITIES and a TEXT (which may be
Vietnamese, English, or mixed), output relationships between those entities.

OUTPUT FORMAT — return exactly this JSON, nothing else:
{{"relationships":[{{"from":"...","to":"...","type":"...","score":0.0}}]}}

STRICT RULES:
1. "from" and "to" values MUST be copied CHARACTER-FOR-CHARACTER from the ENTITIES
   list — every Vietnamese diacritic and tone mark intact. No added words, no
   changed capitalisation, no abbreviations, no translation.
2. "type" is an UPPER_SNAKE_CASE English verb (e.g. USES, MANAGES, ISSUED_BY,
   PART_OF, LOCATED_IN, REGULATES, OWNS, SUPPLIES, REPORTS_TO, COOPERATES_WITH).
   The type is a machine label and is ALWAYS English, even for Vietnamese text.
3. "score" is a number 0.0–1.0 = how clearly the TEXT supports this relationship.
4. Return AT MOST {cfg.MAX_DOC_RELATIONSHIPS} relationships, the most
   clearly-supported first. Return {{"relationships":[]}} only if no two entities
   relate at all.
5. Include relationships that are stated OR clearly implied by the text."""


_CONFLICT_SYSTEM = """\
Compare two knowledge summaries (they may be in Vietnamese or English).
Decide if there is a conflict.
Conflict types and when to use them:
- content_duplicate: summaries describe the exact same or nearly identical information — always has_conflict: true
- content_contradiction: summaries make opposing factual claims
- content_conflict: summaries provide different or incompatible details on the same topic
- content_update: incoming summary is a newer/revised version of the existing one
Severity: low (duplicate/minor), medium (conflict), high (contradiction).
IMPORTANT: if both summaries convey the same information, output has_conflict: true with content_duplicate.
Output a JSON object with keys: has_conflict (bool), conflict_type (string), severity (string), explanation (max 20 words)."""


# ── Vietnamese prompt variants (PROMPT_LANG=vi, default) ──────────────────────
# Native-Vietnamese instructions for a Vietnamese corpus: the English-framed
# prompts showed code-switching slippage on a 7B model (descriptions assigned to
# the wrong entity, hallucinated aliases like "Đức" on "Liên bang Nga"). JSON
# keys and type values stay English — the vLLM schema enforces them anyway.

_DOC_REDUCE_SYSTEM_VI = """\
Bạn tổng hợp các bản tóm tắt từng phần của MỘT tài liệu thành một bản tóm tắt duy nhất.
Đầu vào: danh sách tóm tắt được đánh số, theo thứ tự trong tài liệu.

Chỉ trả về JSON đúng định dạng sau, không thêm gì khác: {"summary":"..."}

QUY TẮC:
1. NGÔN NGỮ — viết bằng đúng ngôn ngữ của đầu vào. KHÔNG dịch.
2. TÊN RIÊNG — chép lại mọi tên riêng (người, tổ chức, địa danh, hệ thống, luật,
   nghị định, dự án, sản phẩm) CHÍNH XÁC TỪNG KÝ TỰ, giữ nguyên toàn bộ dấu tiếng
   Việt ("Đà Nẵng" phải giữ là "Đà Nẵng", không bao giờ viết "Da Nang").
   KHÔNG được bỏ tên riêng để tiết kiệm chỗ: bước trích xuất thực thể chạy trên
   kết quả của bạn — tên nào bị bỏ sẽ mất vĩnh viễn khỏi đồ thị tri thức.
   Ưu tiên cắt câu chữ chung chung thay vì cắt tên riêng.
3. Giữ các con số và mốc thời gian quan trọng của tài liệu.
4. Tối đa 250 từ. Văn xuôi thuần, không markdown, không gạch đầu dòng."""


_DOC_ENTITY_SYSTEM_VI = f"""\
Bạn trích xuất thực thể cho đồ thị tri thức từ bản tóm tắt một tài liệu tiếng Việt
(có thể lẫn tiếng Anh).

Chỉ trả về JSON đúng định dạng sau, không thêm gì khác:
{{"entities":[{{"name":"…","type":"…","aliases":["…"],"description":"…","score":0.0}}]}}
Trả về TỐI ĐA {cfg.MAX_DOC_ENTITIES} thực thể, quan trọng nhất xếp trước.

TYPE — chọn nhãn CỤ THỂ NHẤT phù hợp (giá trị tiếng Anh, cố định). Hãy quyết đoán,
không mặc định "concept" khi có nhãn cụ thể hơn:
  person         — cá nhân CÓ TÊN RIÊNG ("Hồ Chí Minh", "Albert Einstein")
  organization   — công ty, cơ quan nhà nước, tổ chức, triều đình, quân đội,
                   liên đoàn thể thao ("NHNN", "Nội các", "Quân Giải phóng Nhân dân
                   Trung Quốc", "NFL", "Việt Minh")
  location       — quốc gia, thành phố, vùng, địa danh ("Đà Nẵng", "sông Mê Kông").
                   QUỐC GIA LUÔN LÀ location, kể cả khi tham chiến, đàm phán hay
                   hành động như một chính phủ ("Pháp", "Hoa Kỳ", "Liên Xô",
                   "Trung Quốc" → location, KHÔNG BAO GIỜ organization).
                   Đế chế và quốc gia lịch sử cũng là location ("Đế chế Ba Tư",
                   "Đế quốc Đức", "Phổ", "Đế quốc Ottoman" → location).
                   Tên nước ngoài của cùng quốc gia là ALIAS, không phải thực thể
                   riêng ("Deutsches Reich" → alias của "Đế quốc Đức").
  law_document   — luật, nghị định, thông tư, hiệp định, tiêu chuẩn
                   ("Nghị định 13/2023/NĐ-CP", "Hiệp định Geneva")
  system_product — CHỈ hệ thống kỹ thuật, phần mềm, sản phẩm, công trình, tác phẩm
                   CÓ TÊN do con người tạo ra ("Tháp đôi Petronas", "Windows").
                   KHÔNG dùng cho tổ chức, cơ quan, quân đội, giải đấu.
  event          — chiến tranh, chiến dịch, hội nghị, thảm họa có tên
                   ("Chiến tranh thế giới thứ hai", "Chiến dịch Điện Biên Phủ")
  time_period    — triều đại, thời kỳ có tên ("nhà Trần", "thời Pháp thuộc")
  concept        — CHỈ khi không nhãn nào ở trên phù hợp (học thuyết, lĩnh vực,
                   khái niệm, loài vật nổi tiếng có tên)

QUY TẮC VỀ TÊN:
1. Chép "name" CHÍNH XÁC TỪNG KÝ TỰ từ văn bản, giữ nguyên toàn bộ dấu. Dấu thanh
   làm thay đổi nghĩa ("bán" ≠ "bàn" ≠ "bạn") — sai dấu là sai thực thể.
2. KHÔNG dịch tên. "Việt Nam" giữ là "Việt Nam".
3. Dùng dạng đầy đủ / chính thức nhất xuất hiện trong văn bản làm "name".

QUY TẮC VỀ ALIASES — RẤT QUAN TRỌNG:
- "aliases" CHỈ chứa các tên gọi khác của CHÍNH thực thể đó: tên viết tắt, tên rút
  gọn, tên cũ, tên thông dụng. Ví dụ đúng:
    name "Thành phố Hồ Chí Minh" → aliases ["TP.HCM","TPHCM","Sài Gòn"]
    name "Ngân hàng Nhà nước Việt Nam" → aliases ["NHNN"]
    name "Hoa Kỳ" → aliases ["Mỹ"]
- TUYỆT ĐỐI KHÔNG đưa thực thể KHÁC vào aliases. "Đức" KHÔNG phải alias của
  "Liên bang Nga" — đó là hai quốc gia khác nhau. Nếu không chắc chắn 100% hai tên
  cùng chỉ một thực thể, KHÔNG thêm vào aliases. Để [] khi không có.

KHÔNG trích xuất danh từ chung không chỉ đích danh một thực thể nào:
  ✗ hệ thống, phương pháp, mô hình, quy trình, tài liệu, dữ liệu, giải pháp,
    thông tin, nội dung, kết quả, báo cáo, tổ chức, đơn vị, sản phẩm, thủ tướng,
    chính phủ (khi không nói rõ của nước nào)
  ✗ Chức danh, nghề nghiệp, vai trò KHÔNG CÓ TÊN RIÊNG kèm theo: "nhà vua",
    "tộc trưởng", "thợ vàng", "Thủ tướng", "Chủ tịch Hội", "Vua nước Phổ",
    "Hoàng đế Đức", "Lục quân", "Hải quân" — "person" CHỈ dành cho cá nhân có
    tên riêng cụ thể ("Bismarck" ✓, "Hoàng đế Đức" ✗).
  ✗ Cụm danh từ mô tả, không phải TÊN riêng: "Dân số nước này",
    "Hệ thống đường sắt nhà nước", "sinh vật", "thiên thạch".

QUY TẮC BỔ SUNG:
- MỖI thực thể thực tế CHỈ MỘT mục. Các biến thể tên của cùng một thực thể
  ("Viện Khoa học Hoàng gia", "Viện Khoa học Hoàng gia Dublin", "Viện Khoa học
  Hoàng gia Ireland"; "Amur" và "tỉnh Amur"; "gà" và "gà nhà") phải gộp thành
  MỘT mục: dạng đầy đủ nhất làm "name", các biến thể chắc chắn cùng thực thể
  đưa vào "aliases".
- KIỂM TRA LẦN CUỐI trước khi trả lời: nếu hai mục có "description" trùng hoặc
  gần giống nhau → gần như chắc chắn là một thực thể → gộp lại.

TỰ KIỂM TRA từng thực thể: "Tên này có thể là tiêu đề một bài Wikipedia hoặc một
hồ sơ chính thức không?" Nếu không → loại bỏ.

"description": tối đa 12 từ, mô tả ĐÚNG thực thể đó (không lẫn sang thực thể khác),
cùng ngôn ngữ với tài liệu.
"score": 0.0–1.0 — mức độ quan trọng của thực thể đối với toàn tài liệu.

── VÍ DỤ ───────────────────────────────────────────────────────────────────────
Đầu vào: "Ngân hàng Nhà nước Việt Nam (NHNN) ban hành Thông tư 06/2023/TT-NHNN
quy định hoạt động cho vay tại Thành phố Hồ Chí Minh và Hà Nội."
Đầu ra ĐÚNG:
{{"entities":[
 {{"name":"Ngân hàng Nhà nước Việt Nam","type":"organization","aliases":["NHNN"],
  "description":"Ngân hàng trung ương của Việt Nam","score":0.95}},
 {{"name":"Thông tư 06/2023/TT-NHNN","type":"law_document","aliases":[],
  "description":"Thông tư quy định hoạt động cho vay","score":0.9}},
 {{"name":"Thành phố Hồ Chí Minh","type":"location","aliases":["TP.HCM","Sài Gòn"],
  "description":"Thành phố lớn nhất Việt Nam","score":0.6}},
 {{"name":"Hà Nội","type":"location","aliases":[],
  "description":"Thủ đô của Việt Nam","score":0.6}}
]}}
Đầu ra SAI (tuyệt đối tránh):
✗ "Hà Nội" nằm trong aliases của "Thành phố Hồ Chí Minh" — hai thành phố KHÁC nhau
✗ "Thông tư 06/2023/TT-NHNN" nằm trong aliases của "Ngân hàng Nhà nước Việt Nam"
  — văn bản do tổ chức ban hành KHÔNG phải tên khác của tổ chức
✗ description của NHNN ghi "Thông tư quy định cho vay" — mô tả lẫn sang thực thể khác"""


_RELATIONSHIP_SYSTEM_VI = f"""\
Bạn xây dựng cạnh cho đồ thị tri thức. Cho một danh sách THỰC THỂ (ENTITIES) và một
VĂN BẢN (TEXT) tiếng Việt, hãy trả về các mối quan hệ giữa các thực thể đó.

Chỉ trả về JSON đúng định dạng sau, không thêm gì khác:
{{"relationships":[{{"from":"...","to":"...","type":"...","score":0.0}}]}}

QUY TẮC BẮT BUỘC:
1. Giá trị "from" và "to" PHẢI chép CHÍNH XÁC TỪNG KÝ TỰ từ danh sách ENTITIES —
   giữ nguyên mọi dấu tiếng Việt. Không thêm từ, không đổi chữ hoa/thường,
   không viết tắt, không dịch.
2. "type" là một động từ tiếng Anh dạng UPPER_SNAKE_CASE (ví dụ: USES, MANAGES,
   ISSUED_BY, PART_OF, LOCATED_IN, REGULATES, OWNS, FOUNDED, LED_BY,
   COOPERATES_WITH). Nhãn này luôn là tiếng Anh. Tránh nhãn quá chung
   ("IS", "HAS") khi có nhãn cụ thể hơn.
3. CHIỀU quan hệ phải đúng logic: "from" là CHỦ THỂ thực hiện / sở hữu / thuộc về.
   Tự kiểm tra bằng cách đọc thành câu: "nhà Hán PART_OF Trung Quốc" đọc là
   "nhà Hán là một phần của Trung Quốc" → đúng. "Trung Quốc PART_OF nhà Hán" →
   sai chiều. "Charles V USED đồng hồ cát" → đúng;
   "Charles V ISSUED_BY đồng hồ cát" → vô nghĩa.
4. "score" là số 0.0–1.0 = mức độ văn bản ủng hộ rõ ràng mối quan hệ này.
5. Trả về TỐI ĐA {cfg.MAX_DOC_RELATIONSHIPS} mối quan hệ, rõ ràng nhất xếp trước.
   Chỉ trả {{"relationships":[]}} khi thực sự không có cặp thực thể nào liên quan.
6. Bao gồm cả quan hệ được nêu trực tiếp HOẶC được ngụ ý rõ ràng trong văn bản."""


_ALIAS_VERIFY_SYSTEM = """\
You verify entity aliases. For each (name, alias) pair, answer same=true ONLY if
"alias" is another name for EXACTLY the same entity as "name" — an abbreviation,
acronym, shortened, historical or colloquial form. Answer same=false if the alias
names a DIFFERENT entity, even a closely related one (parent organisation,
member, product, license, place inside it, etc.).
Examples: ("Thành phố Hồ Chí Minh","Sài Gòn") → true. ("Hoa Kỳ","Mỹ") → true.
("Liên bang Nga","Đức") → false (different country).
("Giấy phép Tài liệu Tự do GNU","Creative Commons") → false (different license).
Output JSON only: {"pairs":[{"name":"…","alias":"…","same":true}]} — one entry
per input pair, copied character-for-character."""

_ALIAS_VERIFY_SYSTEM_VI = """\
Bạn kiểm chứng alias của thực thể. Với mỗi cặp (name, alias), trả lời same=true
CHỈ KHI "alias" là tên gọi khác của ĐÚNG thực thể "name" — tên viết tắt, tên rút
gọn, tên cũ, tên thông dụng. Trả lời same=false nếu alias chỉ một thực thể KHÁC,
kể cả khi có liên quan chặt chẽ (tổ chức mẹ, thành viên, sản phẩm, giấy phép,
địa danh bên trong, v.v.).
Ví dụ: ("Thành phố Hồ Chí Minh","Sài Gòn") → true. ("Hoa Kỳ","Mỹ") → true.
("Liên bang Nga","Đức") → false (hai quốc gia khác nhau).
("Giấy phép Tài liệu Tự do GNU","Creative Commons") → false (hai giấy phép khác nhau).
Chỉ trả JSON: {"pairs":[{"name":"…","alias":"…","same":true}]} — mỗi cặp đầu vào
một mục, chép chính xác từng ký tự."""


_MERGE_VERIFY_SYSTEM = """\
You decide whether two knowledge-base entries describe EXACTLY the same real-world
entity. Answer same=true only for the same entity under different names
("Hoa Kỳ" ~ "Mỹ" → true). Related but distinct entities are false: two different
countries ("Bangladesh" ~ "Ấn Độ" → false), a person vs their country
("Hốt Tất Liệt" ~ "Nước Mông Cổ" → false), two branches of one institution
("Đại học Quốc gia Hà Nội" ~ "Đại học Quốc gia TP.HCM" → false).
Output JSON only: {"same":true} or {"same":false}."""

_MERGE_VERIFY_SYSTEM_VI = """\
Bạn quyết định xem hai mục tri thức có chỉ CÙNG MỘT thực thể thực tế hay không.
Chỉ trả same=true khi là cùng một thực thể dưới tên khác nhau ("Hoa Kỳ" ~ "Mỹ" →
true). Thực thể liên quan nhưng KHÁC nhau là false: hai quốc gia khác nhau
("Bangladesh" ~ "Ấn Độ" → false), người và quốc gia của họ ("Hốt Tất Liệt" ~
"Nước Mông Cổ" → false), hai chi nhánh của một hệ thống ("Đại học Quốc gia Hà Nội"
~ "Đại học Quốc gia TP.HCM" → false).
Chỉ trả JSON: {"same":true} hoặc {"same":false}."""

_CONFLICT_SYSTEM_VI = """\
So sánh hai bản tóm tắt tri thức (có thể bằng tiếng Việt hoặc tiếng Anh).
Quyết định xem có xung đột (conflict) hay không.
Các loại xung đột và khi nào dùng:
- content_duplicate: hai bản mô tả CÙNG một thông tin hoặc gần như giống hệt nhau
- content_contradiction: hai bản đưa ra các khẳng định TRÁI NGƯỢC nhau về dữ kiện
- content_conflict: hai bản nêu chi tiết KHÁC nhau hoặc không tương thích về cùng chủ đề
- content_update: bản đến là phiên bản MỚI HƠN / đã chỉnh sửa của bản hiện có
Mức độ (severity): low (trùng lặp/nhỏ), medium (xung đột), high (mâu thuẫn).

Ví dụ (Hiện có ↔ Đến → kết quả):
- content_duplicate: "Hà Nội là thủ đô của Việt Nam." ↔ "Thủ đô của Việt Nam là Hà Nội."
  → has_conflict: true, content_duplicate, low
- content_contradiction: "Công ty báo LÃI trong quý 2/2023." ↔ "Công ty báo LỖ trong quý 2/2023."
  → has_conflict: true, content_contradiction, high
- content_conflict: "Dự án khởi công năm 2018 với vốn 500 tỷ đồng." ↔ "Dự án khởi công năm 2019 với vốn 700 tỷ đồng."
  → has_conflict: true, content_conflict, medium
- content_update: "Lương tối thiểu vùng I là 4,42 triệu đồng (2022)." ↔ "Từ 01/7/2024, lương tối thiểu vùng I là 4,96 triệu đồng."
  → has_conflict: true, content_update, medium
- KHÔNG xung đột: "Hà Nội là thủ đô của Việt Nam." ↔ "Đà Nẵng là thành phố ven biển miền Trung Việt Nam."
  → has_conflict: false (hai chủ đề khác nhau, bổ sung cho nhau)

Chỉ đặt has_conflict: true khi thực sự rơi vào MỘT trong các loại trên. Nếu hai bản
nói về chủ đề khác nhau, hoặc bổ sung cho nhau mà không đối nghịch, thì has_conflict: false.
Nếu hai bản truyền đạt cùng một thông tin thì has_conflict: true với content_duplicate.
Chỉ trả về một đối tượng JSON với các khóa: has_conflict (bool), conflict_type (string),
severity (string), explanation (giải thích NGẮN bằng tiếng Việt, tối đa 20 từ)."""


_PROMPTS_BY_LANG: dict[str, dict[str, str]] = {
    "en": {
        "doc-reduce":   _DOC_REDUCE_SYSTEM,
        "doc-entity":   _DOC_ENTITY_SYSTEM,
        "relationship": _RELATIONSHIP_SYSTEM,
        "conflict":     _CONFLICT_SYSTEM,
        "alias-verify": _ALIAS_VERIFY_SYSTEM,
        "merge-verify": _MERGE_VERIFY_SYSTEM,
    },
    "vi": {
        "doc-reduce":   _DOC_REDUCE_SYSTEM_VI,
        "doc-entity":   _DOC_ENTITY_SYSTEM_VI,
        "relationship": _RELATIONSHIP_SYSTEM_VI,
        "conflict":     _CONFLICT_SYSTEM_VI,
        "alias-verify": _ALIAS_VERIFY_SYSTEM_VI,
        "merge-verify": _MERGE_VERIFY_SYSTEM_VI,
    },
}


async def verify_merge_pair(name_a: str, desc_a: str, name_b: str, desc_b: str) -> bool:
    """LLM same-entity judgment for gray-band registry merges. Conservative on
    any failure: False (don't merge — a duplicate node is recoverable, a wrong
    merge corrupts the graph permanently)."""
    user = f'A: "{name_a}" — {desc_a or "(không có mô tả)"}\nB: "{name_b}" — {desc_b or "(không có mô tả)"}'
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["merge-verify"], user, max_tokens=16, system_type="merge-verify",
        )
        return bool(result.get("same", False))
    except Exception as exc:
        log.warning("Gold merge-verify: failed (%s) — NOT merging", exc)
        return False

SYSTEM_PROMPTS: dict[str, str] = _PROMPTS_BY_LANG.get(cfg.PROMPT_LANG, _PROMPTS_BY_LANG["vi"])


# ── Extraction helpers ────────────────────────────────────────────────────────

def _to_score(v: Any, default: float = 0.5) -> float:
    """Parse a model-provided score into a clamped 0.0–1.0 float (default if missing)."""
    try:
        s = float(v)
    except (TypeError, ValueError):
        return default
    if s != s:  # NaN
        return default
    return max(0.0, min(1.0, s))


def _top_by_score(items: list[dict], n: int) -> list[dict]:
    """Keep the n highest-scoring items; ties fall back to original (model) order."""
    if len(items) <= n:
        return items
    ranked = sorted(enumerate(items), key=lambda p: (-p[1].get("score", 0.0), p[0]))
    return [item for _, item in ranked[:n]]


# Closed entity-type vocabulary → Neo4j secondary label. Type is metadata, never
# node identity — a wrong LLM type must not split an entity into two nodes.
ENTITY_TYPE_LABELS: dict[str, str] = {
    "person":         "Person",
    "organization":   "Organization",
    "location":       "Location",
    "law_document":   "LawDocument",
    "system_product": "SystemProduct",
    "event":          "Event",
    "time_period":    "TimePeriod",
    "concept":        "Concept",
}


def _to_entity_type(v: Any) -> str:
    s = str(v or "").strip().lower()
    return s if s in ENTITY_TYPE_LABELS else "concept"


def entities_by_type_fields(
    type_by_canonical: dict[str, str],
    canonical_names: list[str],
) -> dict[str, list[str]]:
    """Per-type Qdrant payload fields (ent_person, ent_location, …).

    Parallel name/type arrays can't be correlated inside a Qdrant filter; one
    field per type makes typed conditions first-class ("ent_person MatchAny […]").
    Every type key is always present — empty list when none — so IsEmpty
    conditions behave predictably across all points.
    """
    fields: dict[str, list[str]] = {f"ent_{t}": [] for t in ENTITY_TYPE_LABELS}
    for can in canonical_names:
        fields[f"ent_{_to_entity_type(type_by_canonical.get(can))}"].append(can)
    return fields


def _to_entity_list(v: Any) -> list[dict[str, Any]]:
    """Parse the doc-entity LLM output: name + type + aliases + description + score.
    Surface forms keep their original diacritics; only comparison uses _fold."""
    if not isinstance(v, list):
        return []
    result = []
    seen: set[str] = set()
    for item in v:
        if isinstance(item, dict):
            name    = str(item.get("name") or "").strip()
            etype   = _to_entity_type(item.get("type"))
            desc    = str(item.get("description") or "").strip()
            score   = _to_score(item.get("score"))
            aliases = [
                a.strip() for a in (item.get("aliases") or [])
                if isinstance(a, str) and a.strip()
            ]
        elif isinstance(item, str):
            name, etype, desc, score, aliases = item.strip(), "concept", "", 0.5, []
        else:
            continue
        if not name or len(name) < 2:
            continue
        if _fold(name) in _GENERIC_BLACKLIST:
            log.debug("Gold: filtered generic entity %r", name)
            continue
        # Aliases that are themselves generic words would poison the alias table.
        aliases = [a for a in aliases if _fold(a) not in _GENERIC_BLACKLIST and _fold(a) != _fold(name)]
        key = _fold(name)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "name": name, "type": etype, "aliases": aliases,
            "description": desc, "score": score,
        })

    # Same-document exclusion: an "alias" that matches ANOTHER extracted entity's
    # name cannot be an alias — the model itself asserted they are distinct
    # entities. Catches association-pollution like "Quỹ Wikimedia" listed as an
    # alias of "Giấy phép Tài liệu Tự do GNU" while also extracted on its own.
    all_names = {_fold(e["name"]) for e in result}
    for e in result:
        kept = [a for a in e["aliases"] if _fold(a) not in all_names]
        if len(kept) != len(e["aliases"]):
            log.info("Gold: dropped %d same-doc entity name(s) from aliases of %r",
                     len(e["aliases"]) - len(kept), e["name"])
        e["aliases"] = kept
    return result


# ── Step 2: map-reduce document summary ───────────────────────────────────────

def _batch_texts(texts: list[str], budget_chars: int) -> list[list[str]]:
    """Group texts into consecutive batches of at most budget_chars characters,
    preserving document order (summaries form a narrative — never shuffle)."""
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for t in texts:
        if current and size + len(t) > budget_chars:
            batches.append(current)
            current, size = [], 0
        current.append(t)
        size += len(t)
    if current:
        batches.append(current)
    return batches


async def _reduce_batch(texts: list[str]) -> str:
    """One LLM reduce call over a batch of summaries. Falls back to joining the
    inputs (truncated) if the model can't produce valid JSON."""
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["doc-reduce"],
            numbered,
            # Vietnamese tokenizes at ~2-3 tokens per syllable — a 250-word summary
            # can exceed 512 tokens and truncate mid-JSON, so give it headroom.
            max_tokens=1024,
            system_type="doc-reduce",
        )
        summary = str(result.get("summary") or "").strip()
        if summary:
            return summary
    except Exception as exc:
        log.warning("Gold reduce: batch reduce failed (%s) — joining inputs", exc)
    return " ".join(texts)[: cfg.DOC_SUMMARY_BATCH_CHARS]


async def _reduce_summaries(
    summaries: list[str],
    emitter: PipelineEventEmitter | None = None,
) -> str:
    """Recursively reduce chunk summaries into one document summary.

    Each pass batches the current texts (document order) and reduces every batch
    with one LLM call. Depth is hard-capped: a small model can return output that
    doesn't shrink, and an unbounded "still too long → recurse" loop would never
    terminate. After the cap, hard-truncate.
    """
    texts = [s for s in summaries if s.strip()]
    if not texts:
        return ""

    depth = 0
    while depth < cfg.DOC_SUMMARY_MAX_DEPTH:
        total = sum(len(t) for t in texts)
        if len(texts) == 1 and total <= cfg.DOC_SUMMARY_TARGET_CHARS:
            break
        batches = _batch_texts(texts, cfg.DOC_SUMMARY_BATCH_CHARS)
        if emitter:
            await emitter.emit("gold.doc.reduce.step", {
                "depth":   depth,
                "inputs":  len(texts),
                "batches": len(batches),
                "chars":   total,
            })
        reduced = [await _reduce_batch(b) for b in batches]
        # No shrink → another pass won't help; stop and truncate below.
        if sum(len(t) for t in reduced) >= total and len(reduced) == len(texts):
            texts = reduced
            break
        texts = reduced
        depth += 1

    doc_summary = " ".join(texts)
    if len(doc_summary) > cfg.DOC_SUMMARY_TARGET_CHARS:
        log.warning(
            "Gold reduce: summary still %d chars after depth %d — hard truncating to %d",
            len(doc_summary), depth, cfg.DOC_SUMMARY_TARGET_CHARS,
        )
        doc_summary = doc_summary[: cfg.DOC_SUMMARY_TARGET_CHARS]
    return doc_summary


# ── Step 3: document-level extraction ─────────────────────────────────────────

async def _verify_aliases(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One batched LLM call: pair-by-pair 'is this alias the SAME entity?' check.
    Verification is far more reliable than generation-time discipline — the model
    knows Creative Commons ≠ GFDL when asked directly. Conservative on failure:
    unverified aliases are dropped (pollution is sticky, a missed alias is not)."""
    pairs = [(e["name"], a) for e in entities for a in e["aliases"]]
    if not pairs:
        return entities
    listing = "\n".join(f'("{n}", "{a}")' for n, a in pairs)
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["alias-verify"],
            listing,
            max_tokens=1024,
            system_type="alias-verify",
        )
        verdicts = {
            (_fold(str(p.get("name"))), _fold(str(p.get("alias")))): bool(p.get("same"))
            for p in result.get("pairs", []) if isinstance(p, dict)
        }
    except Exception as exc:
        log.warning("Gold alias-verify: failed (%s) — dropping all %d alias(es) "
                    "for this document (conservative)", exc, len(pairs))
        for e in entities:
            e["aliases"] = []
        return entities

    dropped = 0
    for e in entities:
        kept = [
            a for a in e["aliases"]
            # Missing verdict counts as unverified → dropped.
            if verdicts.get((_fold(e["name"]), _fold(a)), False)
        ]
        dropped += len(e["aliases"]) - len(kept)
        e["aliases"] = kept
    if dropped:
        log.info("Gold alias-verify: rejected %d/%d proposed alias(es)", dropped, len(pairs))
    return entities


async def _extract_doc_entities(doc_summary: str) -> list[dict[str, Any]]:
    """Entities (with aliases) from the document summary. Two attempts at rising
    temperature; empty list if the model never returns usable JSON. Aliases are
    LLM-verified pair-by-pair when ALIAS_VERIFY is on (default)."""
    for attempt, temp in enumerate([0.1, 0.3]):
        try:
            result, _, _ = await _llm_generate_json(
                SYSTEM_PROMPTS["doc-entity"],
                doc_summary,
                max_tokens=1024,
                temperature=temp,
                system_type="doc-entity",
            )
            entities = _top_by_score(_to_entity_list(result.get("entities")), cfg.MAX_DOC_ENTITIES)
            if entities:
                if cfg.ALIAS_VERIFY:
                    entities = await _verify_aliases(entities)
                return entities
            log.warning("Gold doc-entity: attempt %d returned no entities — retrying", attempt + 1)
        except Exception as exc:
            log.warning("Gold doc-entity: attempt %d failed: %s", attempt + 1, exc)
    return []


def _coerce_entity_name(name: str, entity_names: list[str]) -> str | None:
    """Map an LLM-output entity reference back to one of the given names.
    Unicode-aware so Vietnamese names with padding or case drift still match."""
    s = unicodedata.normalize("NFC", str(name)).strip()
    if s in entity_names:
        return s
    by_fold = {_fold(e): e for e in entity_names}
    hit = by_fold.get(_fold(s))
    if hit:
        return hit
    sf = _fold(s)
    # Word-boundary substring: a known entity appears whole inside the LLM output.
    for ename_fold, ename_orig in by_fold.items():
        if len(ename_fold) >= 3 and re.search(
            r"(?<!\w)" + re.escape(ename_fold) + r"(?!\w)", sf
        ):
            return ename_orig
    return None


async def _extract_doc_relationships(
    doc_summary: str, entity_names: list[str]
) -> list[dict]:
    """Relationships between the doc-level entities, names coerced back onto the
    given list. Empty list when nothing valid comes back."""
    if len(entity_names) < 2:
        return []

    entity_str = ", ".join(f'"{e}"' for e in entity_names)
    user_msg = (
        f"ENTITIES: [{entity_str}]\n\n"
        f"TEXT:\n{doc_summary}\n\n"
        f"Now output the relationships JSON using ONLY the entity names listed in ENTITIES above."
    )

    for attempt, temp in enumerate([0.1, 0.3]):
        try:
            result, _, _ = await _llm_generate_json(
                SYSTEM_PROMPTS["relationship"],
                user_msg,
                max_tokens=1024,
                temperature=temp,
                system_type="relationship",
            )
            rels = result.get("relationships", [])
            if not isinstance(rels, list):
                continue
            valid = []
            for r in rels:
                if not isinstance(r, dict):
                    continue
                f = _coerce_entity_name(str(r.get("from", "")), entity_names)
                t = _coerce_entity_name(str(r.get("to", "")), entity_names)
                rt = str(r.get("type", "")).strip()
                if f and t and rt and f != t:
                    valid.append({"from": f, "to": t, "type": rt, "score": _to_score(r.get("score"))})
            valid = _top_by_score(valid, cfg.MAX_DOC_RELATIONSHIPS)
            if valid:
                return valid
            log.warning("Gold relationships: attempt %d returned no valid rels — retrying", attempt + 1)
        except Exception as exc:
            log.warning("Gold relationships: attempt %d failed: %s", attempt + 1, exc)
    return []


# ── Conflict detection ────────────────────────────────────────────────────────

async def _detect_conflict(existing_summary: str, incoming_summary: str) -> tuple[dict, str]:
    """Returns (conflict_result_dict, raw_llm_answer)."""
    user = (
        f"Existing chunk summary:\n{existing_summary}\n\n"
        f"Incoming chunk summary:\n{incoming_summary}"
    )
    try:
        result, raw, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["conflict"], user, max_tokens=128, system_type="conflict",
        )
        return result, raw
    except Exception as exc:
        log.warning("Gold: conflict detection failed (%s) — treating as no conflict", exc)
        return {"has_conflict": False}, f"ERROR: {exc}"


# ── Resolvers (collection model / knowledge base / document name) ─────────────

async def _get_collection_embed_model_id(
    postgres: PostgresClient,
    collection_name: str,
) -> str:
    """Return the embedding_model_id UUID for a named Qdrant collection.

    The model service receives this UUID and resolves the right model itself.
    Falls back to cfg.EMBED_MODEL_ID if the collection has no model set or
    Postgres is unreachable.
    """
    try:
        async with postgres.get_client() as session:
            row = await session.scalar(
                sa_select(KBQdrantCollectionORM.embedding_model_id)
                .where(KBQdrantCollectionORM.collection_name == collection_name)
                .limit(1)
            )
        if row:
            return str(row)
    except Exception as exc:
        log.warning(
            "Gold: could not get embed model_id for collection '%s': %s — using fallback",
            collection_name, exc,
        )
    return cfg.EMBED_MODEL_ID


# Unscoped documents (no knowledge_base_id) are labelled with this default bucket
# so the knowledge_base field is never blank on points / nodes. Keep in sync with
# migrate_kb_schema.DEFAULT_KB.
DEFAULT_KNOWLEDGE_BASE = "General Knowledge Base"


async def _get_data_row(postgres: PostgresClient, data_id: str) -> tuple[str, str]:
    """Return (document_name, knowledge_base) for the KBData document.
    An unscoped document (null knowledge_base_id) resolves to the default bucket
    DEFAULT_KNOWLEDGE_BASE. Name '' if the row is missing."""
    try:
        async with postgres.get_client() as session:
            row = (await session.execute(
                sa_select(KBDataORM.name, KBDataORM.knowledge_base_id)
                .where(KBDataORM.data_id == uuid.UUID(data_id))
                .limit(1)
            )).first()
        if row:
            return str(row[0] or ""), (str(row[1]) if row[1] else DEFAULT_KNOWLEDGE_BASE)
    except Exception as exc:
        log.warning("Gold: could not resolve KBData row for data_id=%s: %s", data_id, exc)
    return "", DEFAULT_KNOWLEDGE_BASE


# ── Neo4j document-level commit ───────────────────────────────────────────────

async def _neo4j_commit_document(
    neo4j: AsyncDriver,
    tenant_id: str,
    data_id: str,
    entity_data: list[tuple[str, str, str, str, list[float]]],
    relationships: list[dict],
    embed_model_id: str,
    knowledge_base: str = "",
) -> None:
    """Commit the document's entities and relationships.

    entity_data: (canonical, display_name, description, entity_type, vector).
    Entity nodes are keyed by (canonical_name, tenant_id, knowledge_base) and
    SHARED across documents in the KB — cross-document linking is the graph's
    whole job. display_name keeps the original casing and diacritics for UI
    rendering — the canonical key is lowercased but never stripped of diacritics.

    The node stores only the spec fields {canonical_name, display_name,
    description, embedding_model_id, tenant_id, knowledge_base, vector} plus
    entity_type. It deliberately does NOT keep per-document provenance
    (data_ids) — many documents mention the same entity and that list grows
    unbounded — nor an alias list (aliases live in the Postgres KBEntityLookup
    table and the entity_registry Qdrant payload). Orphan cleanup on document
    removal is therefore edge-based (see _neo4j_remove_document).

    entity_type is metadata, NOT identity: stored as a property + a secondary
    label (:Entity:Person etc.) so the same real-world entity mis-typed by the
    LLM in one document can never split into two nodes. An existing specific
    type wins over a new one; only the 'concept' fallback can be upgraded.
    Relationships are keyed by (rel_type, data_id, tenant, kb): one document
    asserts an edge once, and re-processing never duplicates it.
    """
    async with neo4j.session() as session:
        for canonical, display, description, etype, vector in entity_data:
            result = await session.run(
                "MERGE (n:Entity {canonical_name: $name, tenant_id: $tid, knowledge_base: $kb}) "
                "SET n.description = CASE WHEN $desc <> '' THEN $desc ELSE coalesce(n.description, '') END, "
                "    n.display_name = coalesce(n.display_name, $display), "
                "    n.vector = $vec, "
                "    n.embedding_model_id = $emid, "
                # Existing specific type wins; only NULL or the 'concept' fallback upgrades.
                "    n.entity_type = CASE WHEN n.entity_type IS NULL OR n.entity_type = 'concept' "
                "                         THEN $etype ELSE n.entity_type END "
                "RETURN n.entity_type AS effective_type",
                name=canonical, tid=tenant_id, kb=knowledge_base,
                desc=description, display=display, vec=vector,
                emid=embed_model_id, etype=etype,
            )
            record = await result.single()
            effective = _to_entity_type(record["effective_type"] if record else etype)
            # Secondary label from the whitelist only — interpolation is safe because
            # ENTITY_TYPE_LABELS values are code-defined identifiers, never user input.
            label = ENTITY_TYPE_LABELS[effective]
            others = " ".join(f"REMOVE n:{l}" for l in ENTITY_TYPE_LABELS.values() if l != label)
            result = await session.run(
                f"MATCH (n:Entity {{canonical_name: $name, tenant_id: $tid, knowledge_base: $kb}}) "
                f"SET n:{label} {others}",
                name=canonical, tid=tenant_id, kb=knowledge_base,
            )
            await result.consume()
        for rel in relationships:
            from_e   = str(rel.get("from", "")).strip()
            to_e     = str(rel.get("to", "")).strip()
            rel_type = str(rel.get("type", "RELATES")).strip()
            if not from_e or not to_e:
                continue
            result = await session.run(
                "MATCH (a:Entity {canonical_name: $from_e, tenant_id: $tid, knowledge_base: $kb}) "
                "MATCH (b:Entity {canonical_name: $to_e,   tenant_id: $tid, knowledge_base: $kb}) "
                "MERGE (a)-[r:RELATES {rel_type: $rtype, data_id: $did, "
                "                      tenant_id: $tid,  knowledge_base: $kb}]->(b) "
                "SET r.score = $score",
                from_e=from_e, to_e=to_e, rtype=rel_type,
                tid=tenant_id, did=data_id, kb=knowledge_base,
                score=_to_score(rel.get("score")),
            )
            await result.consume()


async def _neo4j_remove_document(
    neo4j: AsyncDriver,
    tenant_id: str,
    data_id: str,
    knowledge_base: str = "",
) -> None:
    """Remove one document's contribution from the graph.

    Provenance is no longer stored per node (data_ids would grow unbounded as
    more documents mention an entity), so cleanup is EDGE-based: delete this
    document's :RELATES edges, then delete any entity node left with no edges at
    all. An edgeless entity node is retrieval-inert — graph_expand reaches and
    seeds nodes only through :RELATES edges — so pruning it loses nothing for
    retrieval while keeping the graph from accumulating dangling nodes."""
    try:
        async with neo4j.session() as session:
            result = await session.run(
                "MATCH ()-[r:RELATES {data_id: $did, tenant_id: $tid}]-() DELETE r",
                did=data_id, tid=tenant_id,
            )
            await result.consume()

            result = await session.run(
                "MATCH (n:Entity {tenant_id: $tid, knowledge_base: $kb}) "
                "WHERE NOT (n)--() "
                "DELETE n",
                tid=tenant_id, kb=knowledge_base,
            )
            await result.consume()
        log.debug("Gold: Neo4j document removal done data_id=%s", data_id)
    except Exception as exc:
        log.error("Gold: Neo4j document removal failed data_id=%s: %s", data_id, exc)


# ── Re-promotion cleanup ──────────────────────────────────────────────────────

async def _cleanup_previous_promotion(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    neo4j: AsyncDriver,
    tenant_id: str,
    data_id: str,
    knowledge_base: str = "",
) -> None:
    """Erase any previous (possibly partial) gold state for this document so a
    retry never duplicates chunk points, blocks, or graph edges. Idempotent."""
    # Chunk points by data_id payload filter.
    try:
        await qdrant.delete(
            collection_name=cfg.QDRANT_COLLECTION,
            points_selector=qmodels.FilterSelector(filter=qmodels.Filter(must=[
                qmodels.FieldCondition(key="data_id", match=qmodels.MatchValue(value=data_id)),
            ])),
        )
    except Exception as exc:
        log.warning("Gold cleanup: chunk point delete failed data_id=%s: %s", data_id, exc)

    # Doc-summary point (point_id == data_id).
    try:
        await qdrant.delete(
            collection_name=cfg.QDRANT_DOC_COLLECTION,
            points_selector=qmodels.PointIdsList(points=[data_id]),
        )
    except Exception as exc:
        log.warning("Gold cleanup: doc point delete failed data_id=%s: %s", data_id, exc)

    await _neo4j_remove_document(neo4j, tenant_id, data_id, knowledge_base)

    # Soft-delete previous text blocks so the rebuilt set is the only live one.
    try:
        async with postgres.get_client() as session:
            await session.execute(
                sa_update(KBTextBlockORM)
                .where(KBTextBlockORM.owner_id == uuid.UUID(data_id))
                .where(KBTextBlockORM.is_deleted.is_(False))
                .values(is_deleted=True)
            )
            await session.commit()
    except Exception as exc:
        log.warning("Gold cleanup: block soft-delete failed data_id=%s: %s", data_id, exc)


# ── Atomic per-chunk commit (Postgres + Qdrant — Neo4j is document-level now) ──

async def _commit_chunk_atomic(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    tenant_id: str,
    data_id: str,
    block_index: int,
    content: str,
    table_involved: bool,
    table: dict | None,
    payload_meta: dict,
    embedding: list[float],
    summary: str,
    approved_by: str,
    embed_model_id: str,
    knowledge_base: str = "",
    entities: list[str] | None = None,
    entity_type_fields: dict | None = None,
    emitter: PipelineEventEmitter | None = None,
) -> str:
    block_id: str | None = None
    qdrant_upserted = False

    try:
        async with postgres.get_client() as session:
            try:
                t_pg = _time.monotonic()
                block_orm = KBTextBlockORM(
                    owner_id=uuid.UUID(data_id),
                    block_index=block_index,
                )
                session.add(block_orm)
                await session.flush()

                version_orm = KBTextBlockVersionORM(
                    block_id=block_orm.block_id,
                    version_number=1,
                    content=content,
                    created_by=approved_by,
                    table_involved=table_involved,
                    payload=payload_meta,
                    is_active=True,
                )
                session.add(version_orm)
                # Flush BEFORE building the table row: version_id is a client-side uuid4
                # default that SQLAlchemy only materializes at flush time. Without this,
                # version_orm.version_id is None and the KBTextTable PK/FK would be null.
                await session.flush()

                if table_involved and table:
                    table_orm = KBTextTableORM(
                        version_id=version_orm.version_id,
                        table_name=table.get("table_name", f"table_{block_index}"),
                        description=table.get("description"),
                        data=table.get("data"),
                    )
                    session.add(table_orm)
                    await session.flush()

                block_id = str(block_orm.block_id)
                pg_ms = round((_time.monotonic() - t_pg) * 1000)

                t_qd = _time.monotonic()
                await qdrant.upsert(
                    collection_name=cfg.QDRANT_COLLECTION,
                    points=[
                        qmodels.PointStruct(
                            id=block_id,
                            vector=embedding,
                            payload={
                                "tenant_id":          tenant_id,
                                "data_id":            data_id,
                                "block_id":           block_id,
                                "summary":            summary,
                                # Per-chunk entities: each chunk point carries its
                                # OWN canonical entity set + typed ent_* fields
                                # (extracted in silver, canonicalized in gold).
                                # intents live on the doc-summary point, not chunks.
                                "entities":           entities or [],
                                **(entity_type_fields or {}),
                                "embedding_model_id": embed_model_id,
                                "knowledge_base":     knowledge_base,
                                "is_deleted":         False,
                            },
                        )
                    ],
                )
                qdrant_upserted = True
                qd_ms = round((_time.monotonic() - t_qd) * 1000)

                await session.commit()
                log.info("Gold: atomic commit OK block_index=%d block_id=%s", block_index, block_id)

                if emitter:
                    await emitter.emit("gold.store.timing", {
                        "index":       block_index,
                        "postgres_ms": pg_ms,
                        "qdrant_ms":   qd_ms,
                    })

                return block_id

            except Exception as exc:
                await session.rollback()

                if qdrant_upserted and block_id:
                    try:
                        await qdrant.delete(
                            collection_name=cfg.QDRANT_COLLECTION,
                            points_selector=qmodels.PointIdsList(points=[block_id]),
                        )
                    except Exception as ce:
                        log.error("Gold: Qdrant rollback FAILED block_id=%s: %s", block_id, ce)

                raise RuntimeError(
                    f"Atomic commit failed block_index={block_index}: {exc}"
                ) from exc

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Unexpected error during atomic commit block_index={block_index}: {exc}"
        ) from exc


# ── Legacy-staging compat ─────────────────────────────────────────────────────

def _heuristic_summary(content: str) -> str:
    """Staging docs produced before silver wrote summaries have no chunk summary.
    First sentence, truncated — re-run silver for proper LLM summaries."""
    first_sentence = re.split(r"[.!?…]", content, maxsplit=1)[0].strip()
    return " ".join((first_sentence or content).split()[:40])


# ── Main stage function ────────────────────────────────────────────────────────

async def promote_to_gold(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    neo4j: AsyncDriver,
    mongo: MongoClient,
    model: ModelServiceClient,
    data_id: str,
    tenant_id: str,
    approved_by: str,
    emitter: PipelineEventEmitter | None = None,
) -> dict:
    """
    Per-chunk: embed + conflict detect + commit (Postgres + Qdrant).
    Per-document: reduce summaries → extract entities/relations → canonicalize →
    Neo4j → doc-summary point (LAST) → tier GOLD.
    Returns: {data_id, committed, conflicted, skipped, failed, layer}
    """
    # Resolve the collection's embedding model_id once per task.
    # Stamped onto every Qdrant point, Neo4j entity node, and chunk version payload.
    embed_model_id = await _get_collection_embed_model_id(postgres, cfg.QDRANT_COLLECTION)
    log.info("Gold: collection '%s' → embed_model_id=%s", cfg.QDRANT_COLLECTION, embed_model_id or "none")

    doc_name, knowledge_base = await _get_data_row(postgres, data_id)
    log.info("Gold: data_id=%s → knowledge_base=%s", data_id, knowledge_base or "none")

    mongo_db = mongo.get_client()
    staging = await mongo_db["kb_silver_staging"].find_one({"data_id": data_id})
    if not staging:
        raise ValueError(f"No silver staging found for data_id={data_id}")

    chunks: list[dict] = staging.get("chunks", [])
    log.info("Gold: processing %d chunks data_id=%s", len(chunks), data_id)

    if emitter:
        await emitter.emit("gold.start", {"total_chunks": len(chunks)})

    # Erase any previous (possibly partial) promotion so retries never duplicate.
    await _cleanup_previous_promotion(postgres, qdrant, neo4j, tenant_id, data_id, knowledge_base)

    committed_count  = 0
    conflicted_count = 0
    pending_conflict_count = 0   # conflicts that still need human resolution (excludes auto-resolved duplicates)
    failed_count     = 0
    skipped_count    = 0
    committed_summaries: list[str] = []   # document order — feeds the reduce step
    intent_counter: Counter[str] = Counter()

    # Per-chunk entities are canonicalized + stamped on each chunk during the loop;
    # the document graph (Neo4j) is the UNION of those canonical entities across
    # all committed chunks. These accumulate that union (+ display/desc/type and the
    # raw→canonical map used to remap relationships).
    doc_canonical_set: set[str] = set()
    doc_raw_to_canonical: dict[str, str] = {}
    doc_display_by_canonical: dict[str, str] = {}
    doc_desc_by_canonical: dict[str, str] = {}
    doc_type_by_canonical: dict[str, str] = {}

    # One batch per gold pipeline run for this document — created upfront so the
    # batch row IS the unit of work for the document's promotion, whether or not
    # any conflict is detected. Status is reconciled at the end (Phase 7): it
    # stays PENDING only if real (non-duplicate) conflicts await resolution,
    # otherwise it's marked RESOLVED so clean runs don't linger as pending.
    conflict_batch_id: str | None = None
    batch_res = await postgres.insert(KBConflictBatchInsert(
        tenant_id=tenant_id,
        batch_title=f"Gold pipeline · {doc_name or data_id}",
        status=ConflictStatus.PENDING,
    ))
    if batch_res.code == 200:
        conflict_batch_id = batch_res.data["batch_id"]
    else:
        log.warning("Gold: conflict batch creation failed data_id=%s code=%s", data_id, batch_res.code)

    # ── Phase 1: per-chunk embed → conflict check → commit ────────────────────
    for chunk in chunks:
        block_index: int  = chunk["block_index"]
        content: str      = chunk["content"]
        table_involved    = chunk.get("table_involved", False)
        table: dict | None = chunk.get("table")
        summary: str      = (chunk.get("summary") or "").strip() or _heuristic_summary(content)
        intents: list[str] = [
            s for s in (chunk.get("intents") or []) if isinstance(s, str) and s.strip()
        ] or ["document_content"]

        if emitter:
            await emitter.emit("gold.chunk.start", {
                "index":   block_index,
                "content": content,
                "length":  len(content),
            })

        try:
            # ── Embed the chunk text (the vector IS the textblock) ────────────
            t_emb = _time.monotonic()
            embedding = (await model.embed([content[:4000]], model_id=embed_model_id))[0]
            emb_ms = round((_time.monotonic() - t_emb) * 1000)

            if emitter:
                await emitter.emit("gold.embed.complete", {
                    "index":       block_index,
                    "dim":         len(embedding),
                    "duration_ms": emb_ms,
                })

            # ── Conflict detection ────────────────────────────────────────────
            t_cf = _time.monotonic()
            search_res = await qdrant_vector_search(qdrant, SearchRequest(
                tenant_id=tenant_id,
                collection_name=cfg.QDRANT_COLLECTION,
                query_vector=embedding,
                limit=1,
                knowledge_base=knowledge_base or None,
            ))

            hits = search_res.data if search_res.code == 200 and search_res.data else []
            candidates = len(hits)
            top_score  = max((h.score for h in hits), default=0.0)
            if emitter:
                await emitter.emit("gold.conflict.check", {
                    "index":      block_index,
                    "candidates": candidates,
                    "threshold":  cfg.CONFLICT_SIMILARITY_THRESHOLD,
                    "top_score":  round(top_score, 4),
                })

            conflict_found = False
            # Top-1 candidate only. If it clears the similarity floor, the LLM
            # decides whether it is genuinely a conflict — nothing is auto-resolved,
            # so every confirmed conflict (duplicates included) surfaces as PENDING.
            for hit in hits:
                if hit.score < cfg.CONFLICT_SIMILARITY_THRESHOLD:
                    continue
                existing_summary = (hit.payload or {}).get("summary", "")
                conflict_result, conflict_raw = await _detect_conflict(existing_summary, summary)
                if conflict_result.get("has_conflict"):
                    raw_type     = conflict_result.get("conflict_type", ConflictType.CONTENT_CONFLICT.value)
                    raw_severity = conflict_result.get("severity", ConflictSeverity.MEDIUM.value)
                    try:
                        conflict_type_enum = ConflictType(raw_type)
                    except ValueError:
                        conflict_type_enum = ConflictType.CONTENT_CONFLICT
                    try:
                        severity_enum = ConflictSeverity(raw_severity)
                    except ValueError:
                        severity_enum = ConflictSeverity.MEDIUM

                    await postgres.insert(KBConflictInsert(
                        tenant_id=tenant_id,
                        conflict_type=conflict_type_enum,
                        severity=severity_enum,
                        batch_id=conflict_batch_id,
                        status=ConflictStatus.PENDING,
                        detailed_explanation=conflict_result.get("explanation", ""),
                        existing_snapshot={
                            "block_id": str(hit.id),
                            "summary":  existing_summary,
                        },
                        incoming_snapshot={
                            "block_index":    block_index,
                            "data_id":        data_id,
                            "tenant_id":      tenant_id,
                            "approved_by":    approved_by,
                            "content":        content,
                            "summary":        summary,
                            "embedding":      embedding,
                            "intents":        intents,
                            "table_involved": table_involved,
                            "table":          table,
                            "embed_model_id": embed_model_id,
                            "knowledge_base": knowledge_base,
                        },
                    ))
                    conflict_found = True
                    conflicted_count += 1
                    pending_conflict_count += 1

                    if emitter:
                        await emitter.emit("gold.conflict.found", {
                            "index":            block_index,
                            "conflict_type":    raw_type,
                            "severity":         raw_severity,
                            "explanation":      conflict_result.get("explanation", ""),
                            "existing_summary": existing_summary,
                            "similarity":       round(hit.score, 4),
                            "raw_answer":       conflict_raw[:2000],
                        })

                    log.info(
                        "Gold: conflict detected block_index=%d type=%s severity=%s",
                        block_index, raw_type, raw_severity,
                    )
                    break

            if emitter:
                await emitter.emit("gold.conflict.timing", {
                    "index":       block_index,
                    "candidates":  candidates,
                    "duration_ms": round((_time.monotonic() - t_cf) * 1000),
                })

            if conflict_found:
                continue

            # ── Per-chunk entities: filter (blacklist/dedup) → canonicalize →
            #    accumulate into the document-level union for the graph ──────────
            raw_ents = _to_entity_list(chunk.get("entities") or [])
            chunk_canonicals: list[str] = []
            chunk_type_by_can: dict[str, str] = {}
            if raw_ents:
                try:
                    chunk_canonicals, chunk_details = await entity_registry.normalize_entities_detailed(
                        qdrant, postgres, model, tenant_id, raw_ents,
                        verify_fn=verify_merge_pair,
                        knowledge_base=knowledge_base, embed_model_id=embed_model_id,
                    )
                except Exception as exc:
                    log.warning("Gold: per-chunk canonicalize failed block_index=%d: %s", block_index, exc)
                    chunk_details = []
                local_raw_to_can: dict[str, str] = {}
                for d in chunk_details:
                    can = d["canonical"]
                    for form in [d["raw"], *d.get("aliases", [])]:
                        local_raw_to_can[_fold(form)] = can
                        doc_raw_to_canonical[_fold(form)] = can
                    doc_display_by_canonical.setdefault(can, d["raw"])
                for raw_ent in raw_ents:
                    can = local_raw_to_can.get(_fold(raw_ent["name"]))
                    if not can:
                        continue
                    chunk_type_by_can.setdefault(can, raw_ent.get("type", "concept"))
                    doc_desc_by_canonical.setdefault(can, raw_ent.get("description", ""))
                    doc_type_by_canonical.setdefault(can, raw_ent.get("type", "concept"))
                doc_canonical_set.update(chunk_canonicals)
                if emitter:
                    await emitter.emit("gold.chunk.entities", {
                        "index": block_index, "entities": chunk_canonicals,
                    })

            # ── Atomic commit (Postgres + Qdrant) ─────────────────────────────
            # Each chunk carries its OWN canonical entities + typed ent_* fields.
            # intents are a DOCUMENT-level field (KBData.doc_metadata), not per-chunk.
            payload_meta = {
                "summary":            summary,
                "entities":           chunk_canonicals,
                "embedding_model_id": embed_model_id,
                "knowledge_base":     knowledge_base,
            }

            block_id = await _commit_chunk_atomic(
                postgres=postgres,
                qdrant=qdrant,
                tenant_id=tenant_id,
                data_id=data_id,
                block_index=block_index,
                content=content,
                table_involved=table_involved,
                table=table,
                payload_meta=payload_meta,
                embedding=embedding,
                summary=summary,
                approved_by=approved_by,
                embed_model_id=embed_model_id,
                knowledge_base=knowledge_base,
                entities=chunk_canonicals,
                entity_type_fields=entities_by_type_fields(chunk_type_by_can, chunk_canonicals),
                emitter=emitter,
            )
            committed_count += 1
            committed_summaries.append(summary)
            intent_counter.update(intents)

            if emitter:
                await emitter.emit("gold.commit.ok", {
                    "index":              block_index,
                    "block_id":           block_id,
                    "embedding_model_id": embed_model_id,
                })

        except Exception as exc:
            failed_count += 1
            log.error("Gold: chunk failed block_index=%d data_id=%s: %s", block_index, data_id, exc)
            if emitter:
                await emitter.emit("gold.chunk.failed", {
                    "index": block_index,
                    "error": str(exc)[:300],
                })

    # Document-level intents = the most common chunk intents. Mirrors the doc
    # point's `intents` and is persisted on KBData.doc_metadata in Phase 7.
    top_intents = [i for i, _ in intent_counter.most_common(5)]

    # ── Phases 2–6: document rollup → extraction → graph → doc point ──────────
    # Any failure here propagates: chunks stay committed, but the doc-summary
    # point is never written, so the document remains invisible to 2-step
    # retrieval and the tier stays SILVER. Re-promotion cleans up and retries.
    if committed_summaries:
        # Phase 2: map-reduce document summary
        if emitter:
            await emitter.emit("gold.doc.reduce.start", {"summaries": len(committed_summaries)})
        doc_summary = await _reduce_summaries(committed_summaries, emitter)
        if emitter:
            await emitter.emit("gold.doc.reduce.complete", {
                "summary": doc_summary,
                "chars":   len(doc_summary),
            })

        # Phase 3: the document's entity set is the UNION of the per-chunk
        # entities canonicalized during the loop — NOT a separate doc-level
        # extraction. Relationships are still extracted once from the document
        # summary, scoped to those union entities, then remapped to canonicals.
        canonical_names      = sorted(doc_canonical_set)
        raw_to_canonical     = doc_raw_to_canonical
        display_by_canonical = doc_display_by_canonical
        desc_by_canonical    = doc_desc_by_canonical
        type_by_canonical    = doc_type_by_canonical

        rel_names = [display_by_canonical.get(can, can) for can in canonical_names]
        relationships = (
            await _extract_doc_relationships(doc_summary, rel_names)
            if len(canonical_names) >= 2 else []
        )
        if emitter:
            await emitter.emit("gold.doc.extract.complete", {
                "entities":      canonical_names,
                "relationships": relationships,
            })

        remapped_rels: list[dict] = []
        canonical_set = set(canonical_names)
        for rel in relationships:
            can_from = raw_to_canonical.get(_fold(str(rel.get("from", ""))))
            can_to   = raw_to_canonical.get(_fold(str(rel.get("to", ""))))
            if can_from in canonical_set and can_to in canonical_set and can_from != can_to:
                remapped_rels.append({**rel, "from": can_from, "to": can_to})

        # Phase 5: one embed call — doc summary + entity descriptions
        desc_texts = [
            desc_by_canonical.get(can) or display_by_canonical.get(can, can).replace("_", " ")
            for can in canonical_names
        ]
        vectors = await model.embed([doc_summary] + desc_texts, model_id=embed_model_id)
        doc_embedding  = vectors[0]
        entity_vectors = vectors[1:]

        entity_data: list[tuple[str, str, str, str, list[float]]] = [
            (
                can,
                display_by_canonical.get(can, can),
                desc_by_canonical.get(can, ""),
                type_by_canonical.get(can, "concept"),
                vec,
            )
            for can, vec in zip(canonical_names, entity_vectors)
        ]

        # Phase 6a: Neo4j document graph
        await _neo4j_commit_document(
            neo4j, tenant_id, data_id, entity_data, remapped_rels,
            embed_model_id=embed_model_id,
            knowledge_base=knowledge_base,
        )
        if emitter:
            await emitter.emit("gold.doc.graph.ok", {
                "neo4j_nodes": len(entity_data),
                "neo4j_edges": len(remapped_rels),
            })

        # NOTE: entities + typed ent_* fields are now stamped on each chunk's
        # Qdrant point AND its Postgres version payload at commit time (per-chunk),
        # so there is no doc-level back-stamp here anymore.

        # Phase 6b: doc-summary point — written LAST. This is what makes the
        # document visible to step-1 retrieval; everything before it can be
        # retried invisibly.
        try:
            await qdrant.upsert(
                collection_name=cfg.QDRANT_DOC_COLLECTION,
                points=[qmodels.PointStruct(
                    id=data_id,
                    vector=doc_embedding,
                    payload={
                        "tenant_id":          tenant_id,
                        "data_id":            data_id,
                        "summary":            doc_summary,
                        # The doc point is the entry point for step-1 retrieval and
                        # is now narrowed by INTENT (chunks carry the entities). No
                        # entity / ent_* fields live here anymore.
                        "intents":            top_intents,
                        "chunk_count":        committed_count,
                        "embedding_model_id": embed_model_id,
                        "knowledge_base":     knowledge_base,
                        "is_deleted":         False,
                    },
                )],
            )
        except Exception as exc:
            # Doc point failed after the graph commit — undo the graph so a retry
            # starts clean, then surface the failure.
            await _neo4j_remove_document(neo4j, tenant_id, data_id, knowledge_base)
            raise RuntimeError(f"Doc-summary point upsert failed data_id={data_id}: {exc}") from exc

        if emitter:
            await emitter.emit("gold.doc.commit.ok", {
                "data_id":  data_id,
                "entities": canonical_names,
                "intents":  top_intents,
            })
    else:
        log.warning(
            "Gold: no committed chunks for data_id=%s — skipping document rollup "
            "(no doc point, document invisible to 2-step retrieval)",
            data_id,
        )

    # ── Phase 7: update document tier + stamp document-level intents ──────────
    # intents live on the document row (KBData.doc_metadata) — a document concept
    # mirroring the doc-summary Qdrant point. jsonb merge preserves {type: ...}.
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBDataORM)
            .where(KBDataORM.data_id == uuid.UUID(data_id))
            .values(
                current_tier=Tier.GOLD.value,
                # Pass the dict to the JSONB cast directly (encoded once by the
                # type). json.dumps here would double-encode into a string scalar,
                # turning the merge into array concat and corrupting doc_metadata.
                doc_metadata=sa_func.coalesce(
                    KBDataORM.doc_metadata, sa_cast({}, JSONB)
                ).op("||")(sa_cast({"intents": top_intents}, JSONB)),
            )
        )
        await session.commit()

    # Reconcile this run's batch status: PENDING only while real (non-duplicate)
    # conflicts await human resolution; otherwise the run is clean → RESOLVED.
    # Keeps the batch row's status meaningful now that every document gets one.
    if conflict_batch_id is not None:
        final_batch_status = (
            ConflictStatus.PENDING.value if pending_conflict_count > 0
            else ConflictStatus.RESOLVED.value
        )
        try:
            async with postgres.get_client() as session:
                await session.execute(
                    sa_update(KBConflictBatchORM)
                    .where(KBConflictBatchORM.batch_id == uuid.UUID(conflict_batch_id))
                    .values(status=final_batch_status)
                )
                await session.commit()
        except Exception as exc:
            log.warning("Gold: batch status reconcile failed batch_id=%s: %s", conflict_batch_id, exc)

    await postgres.insert(KBLifecycleHistoryInsert(
        data_id=data_id,
        to_tier=Tier.GOLD,
        from_tier=Tier.SILVER,
        approved_by=approved_by,
        notes=(
            f"Promoted to gold: {committed_count} committed, "
            f"{conflicted_count} conflicts, {skipped_count} skipped, {failed_count} failed"
        ),
        pipeline_metadata={
            "stage":             "gold",
            "chunks_committed":  committed_count,
            "chunks_conflicted": conflicted_count,
            "chunks_skipped":    skipped_count,
            "chunks_failed":     failed_count,
            "embed_model_id":    embed_model_id,
        },
    ))

    log.info(
        "Gold complete data_id=%s committed=%d conflicted=%d skipped=%d failed=%d",
        data_id, committed_count, conflicted_count, skipped_count, failed_count,
    )

    if emitter:
        await emitter.emit("gold.complete", {
            "committed":  committed_count,
            "conflicted": conflicted_count,
            "skipped":    skipped_count,
            "failed":     failed_count,
        })
        await emitter.emit("pipeline.done", {})

    return {
        "data_id":    data_id,
        "committed":  committed_count,
        "conflicted": conflicted_count,
        "skipped":    skipped_count,
        "failed":     failed_count,
        "layer":      Tier.GOLD.value,
    }
