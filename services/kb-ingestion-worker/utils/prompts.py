"""All LLM system prompts for the ingestion pipeline, in one place.

silver/gold/conflict_resolver import the selected prompt sets from here instead of
carrying hundreds of lines of prompt text inline. Selection follows cfg.PROMPT_LANG
("vi" default, "en" fallback); JSON keys and type enum values stay English either
way (the model-service schema enforces them).

Prompt text is tuned — edit with care. The `system_type` keys here MUST stay in
sync with utils.llm_schemas.SCHEMAS_BY_SYSTEM_TYPE so the gateway forwards the
matching response_format.
"""
from __future__ import annotations

from utils import config as cfg


# ═══════════════════════════════════════════════════════════════════════════════
# SILVER — per-chunk moderation + summary/entity extraction
# ═══════════════════════════════════════════════════════════════════════════════

_MODERATE_SYSTEM = (
    "You are a content moderation assistant. "
    "Given a list of policy rules and a text chunk, decide if the chunk violates any rule. "
    'Reply ONLY with valid JSON: {"blocked": true/false, "reason": "..."}.'
)

_CHUNK_SUMMARY_SYSTEM = """\
You summarize text chunks for a knowledge base. Documents may be in Vietnamese, \
English, or mixed. Return JSON with these keys: summary, entities, relationships, \
intents, skip.

── summary ─────────────────────────────────────────────────────────────────────
1–2 sentences (≤ 40 words) capturing the most important facts of the chunk.
LANGUAGE RULE: write the summary in the SAME language as the chunk — a Vietnamese
chunk gets a Vietnamese summary. NEVER translate.
NAME RULE: copy proper names (people, organisations, places, systems, laws,
projects) CHARACTER-FOR-CHARACTER, keeping ALL Vietnamese diacritics and tone
marks exactly as written ("Đà Nẵng" stays "Đà Nẵng", never "Da Nang"). Tone marks
change meaning in Vietnamese — altering them corrupts the entity.
Prefer keeping concrete names, numbers, and dates over generic phrasing: these
summaries are later merged into a document summary.

── entities (0–8 knowledge-graph entities that appear IN THIS CHUNK) ─────────────
The specific named things this chunk is about. For each: name, type, description
(≤ 12 words), score (0.0–1.0 importance in this chunk).
LANGUAGE RULE: copy each name in the chunk's OWN language, CHARACTER-FOR-CHARACTER
with ALL Vietnamese diacritics ("Đà Nẵng" stays "Đà Nẵng"). NEVER translate a name
and NEVER output Chinese / Han characters unless they literally appear in the chunk.
type — exactly one of: person, organization, location, law_document,
system_product, event, time_period, concept (use "concept" only when none fit).
Skip generic nouns (system, method, data, document, report, …). Return [] if none.

── relationships (0–6 relationships BETWEEN the entities above) ──────────────────
Only relationships the chunk TEXT explicitly states or clearly implies — this chunk
is the evidence. For each: from, to (BOTH must be names from `entities` above),
type (a short snake_case verb phrase, e.g. works_for, located_in, owns, part_of,
issued_by, succeeded_by), score (0.0–1.0 confidence). Do NOT invent relationships
not supported by the text. Return [] if none.

── intents (2–4, snake_case English labels) ────────────────────────────────────
Machine-readable purpose labels (ALWAYS English snake_case, even for Vietnamese
text — they are filter keys, not display text). Choose from:
  information_retrieval, regulation_compliance, technical_specification,
  financial_reporting, process_description, organisational_info,
  question_answering, data_definition, historical_record, announcement
Add a custom snake_case label only if none fits.

── skip (boolean) ──────────────────────────────────────────────────────────────
true ONLY for chunks with NO indexable content: pure reference lists, isolated
headers or page numbers, acknowledgements, legal boilerplate. Otherwise false.

Output JSON only — no markdown, no extra text:
{"summary":"…","entities":[{"name":"…","type":"…","description":"…","score":0.0}],"relationships":[{"from":"…","to":"…","type":"…","score":0.0}],"intents":["…"],"skip":false}"""

_CHUNK_SUMMARY_SYSTEM_VI = """\
Bạn tóm tắt các đoạn văn bản cho một cơ sở tri thức. Văn bản có thể là tiếng Việt,
tiếng Anh hoặc lẫn cả hai. Chỉ trả về JSON với các khóa: summary, entities,
relationships, intents, skip.

── summary ─────────────────────────────────────────────────────────────────────
1–2 câu (tối đa 40 từ) nêu các thông tin quan trọng nhất của đoạn.
NGÔN NGỮ: viết bằng ĐÚNG ngôn ngữ của đoạn văn — đoạn tiếng Việt thì tóm tắt
tiếng Việt. KHÔNG dịch.
TÊN RIÊNG: chép chính xác từng ký tự (người, tổ chức, địa danh, hệ thống, luật,
dự án), giữ nguyên toàn bộ dấu ("Đà Nẵng" giữ là "Đà Nẵng", không viết "Da Nang").
Dấu thanh làm thay đổi nghĩa — sai dấu là sai thực thể.
Ưu tiên giữ tên riêng, con số, mốc thời gian thay vì câu chữ chung chung: các tóm
tắt này sẽ được gộp thành tóm tắt tài liệu.

── entities (0–8 thực thể tri thức XUẤT HIỆN TRONG ĐOẠN NÀY) ─────────────────────
Những thứ có tên cụ thể mà đoạn nói đến. Mỗi thực thể: name, type, description
(≤ 12 từ), score (0.0–1.0, mức quan trọng trong đoạn).
NGÔN NGỮ: chép tên theo ĐÚNG ngôn ngữ của đoạn, từng ký tự, giữ nguyên mọi dấu
tiếng Việt ("Đà Nẵng" giữ là "Đà Nẵng"). TUYỆT ĐỐI KHÔNG dịch tên và KHÔNG xuất
ký tự Trung/Hán trừ khi chúng thực sự có trong đoạn.
type — đúng một trong: person, organization, location, law_document,
system_product, event, time_period, concept (chỉ dùng "concept" khi không hợp loại nào).
Bỏ qua danh từ chung chung (hệ thống, phương pháp, dữ liệu, tài liệu…). Không có thì trả [].

── relationships (0–6 quan hệ GIỮA các thực thể ở trên) ─────────────────────────
Chỉ những quan hệ mà NỘI DUNG đoạn nêu rõ hoặc hàm ý rõ ràng — đoạn này là bằng
chứng. Mỗi quan hệ: from, to (CẢ HAI phải là tên trong `entities` ở trên), type
(cụm động từ ngắn snake_case, ví dụ: works_for, located_in, owns, part_of,
issued_by, succeeded_by), score (0.0–1.0 độ tin cậy). KHÔNG bịa quan hệ không có
trong văn bản. Không có thì trả [].

── intents (2–4 nhãn, tiếng Anh snake_case) ────────────────────────────────────
Nhãn mục đích cho máy đọc (LUÔN là tiếng Anh snake_case, kể cả với văn bản tiếng
Việt — đây là khóa lọc, không phải văn hiển thị). Chọn trong:
  information_retrieval, regulation_compliance, technical_specification,
  financial_reporting, process_description, organisational_info,
  question_answering, data_definition, historical_record, announcement
Chỉ thêm nhãn tự đặt (snake_case) khi không nhãn nào phù hợp.

── skip (boolean) ──────────────────────────────────────────────────────────────
true CHỈ KHI đoạn không có nội dung đáng lập chỉ mục: danh mục tài liệu tham khảo,
tiêu đề/số trang đứng một mình, lời cảm ơn, văn bản pháp lý rập khuôn. Còn lại: false.

Chỉ trả JSON — không markdown, không văn bản thừa:
{"summary":"…","entities":[{"name":"…","type":"…","description":"…","score":0.0}],"relationships":[{"from":"…","to":"…","type":"…","score":0.0}],"intents":["…"],"skip":false}"""

SILVER_SYSTEM_PROMPTS: dict[str, str] = {
    "moderate":      _MODERATE_SYSTEM,
    "chunk-summary": {"en": _CHUNK_SUMMARY_SYSTEM, "vi": _CHUNK_SUMMARY_SYSTEM_VI}.get(
        cfg.PROMPT_LANG, _CHUNK_SUMMARY_SYSTEM_VI),
}


# ═══════════════════════════════════════════════════════════════════════════════
# GOLD — document reduce / entity / relationship / conflict / verify
# ═══════════════════════════════════════════════════════════════════════════════

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

# ── Vietnamese variants (PROMPT_LANG=vi, default) ──────────────────────────────
# Native-Vietnamese instructions for a Vietnamese corpus: the English-framed
# prompts showed code-switching slippage on a 7B model (descriptions assigned to
# the wrong entity, hallucinated aliases like "Đức" on "Liên bang Nga").

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

_GOLD_PROMPTS_BY_LANG: dict[str, dict[str, str]] = {
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

GOLD_SYSTEM_PROMPTS: dict[str, str] = _GOLD_PROMPTS_BY_LANG.get(
    cfg.PROMPT_LANG, _GOLD_PROMPTS_BY_LANG["vi"])

RECONCILE_RELATIONSHIPS_SYSTEM = """\
You reconcile the relationships of a knowledge graph for ONE document. You are given the
document's ENTITIES, a list of CANDIDATE relationships already extracted from the source
chunks (this is the evidence), and the DOCUMENT SUMMARY. Produce the final, clean set.

RULES:
- Use ONLY entity names from ENTITIES for "from" and "to".
- You MAY merge duplicate / near-duplicate edges, DROP edges that are spurious or that the
  summary contradicts, and FIX the direction or type when the summary makes it clear.
- Do NOT invent relationships that are absent from CANDIDATES.
- "type" is a short snake_case verb phrase; "score" is 0.0-1.0.
Output JSON only: {"relationships":[{"from":"…","to":"…","type":"…","score":0.0}]}"""


# ═══════════════════════════════════════════════════════════════════════════════
# CONFLICT RESOLVER — LLM merge of two conflicting chunks
# ═══════════════════════════════════════════════════════════════════════════════

MERGE_SYSTEM = """\
You are a knowledge editor. Two text chunks contain related but conflicting information.
Merge them into a single coherent chunk that preserves the most important facts from both.
If a resolution instruction is provided, follow it strictly.
Return only the merged text — no commentary, no headers."""
