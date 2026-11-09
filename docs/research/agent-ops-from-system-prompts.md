# Vận hành hệ thống: học từ system prompt agent hàng đầu

> Ngày: 09/11/2026. Đợt 1 (chỉ thiết kế): rút nguyên tắc vận hành từ
> [asgeirtj/system_prompts_leaks](https://github.com/asgeirtj/system_prompts_leaks)
> (Cursor, Claude Code, Codex, Perplexity Deep Research, Anthropic research mode), đối chiếu
> [docs/v3/00-product-strategy.md](../v3/00-product-strategy.md) và
> [docs/v3/01-agent-core-and-ui.md](../v3/01-agent-core-and-ui.md), và runtime Ask hiện tại
> (`backend/services/worker_agent/agentic/`).
>
> **Licence / clean-room:** chỉ học *cách vận hành* (policy, vòng lặp, phân vai). Không copy
> nguyên văn prompt vào product; wording sản phẩm vẫn do ta viết (cùng kỷ luật với
> `docs/research/licences.md`). Nguồn trong repo leaks là tài liệu nghiên cứu, không phải API
> chính thức của từng hãng.

## 0. Kết luận một trang

Các agent hàng đầu **không thắng vì prompt dài**, mà vì harness vận hành:

1. **Chế độ / skill trước tool** — chọn phương pháp (plan, research, explore) rồi mới gọi công cụ.
2. **Phân vai cứng** — sub-agent có `whenToUse`, allowlist/denylist tool, ngữ cảnh hẹp; lead chỉ thấy tóm tắt.
3. **Clarify ít và đúng lúc** — tối đa ~3 câu; chỉ khi đáp án đổi hướng nghiên cứu; mặc định giả định hợp lý.
4. **Bằng chứng trước khẳng định** — bắt buộc tool/observation trước câu trả lời có số hoặc claim.
5. **Bán kính phá hủy** — đọc/local tự do; ghi / kênh ngoài / irreversible → xác nhận hoặc HITL.
6. **Hai kênh giao tiếp** — tiến độ (commentary) ≠ câu trả lời cuối (final, tự chứa).
7. **Verify theo rủi ro** — test/UI/check trước khi tuyên bố xong; fail-fast có chỉ thị, không retry mù.
8. **Memory có kiểu + duyệt** — feedback/project rules bền chỉ sau khi người xác nhận (khớp bất biến 12).

Data Agent V3 **đã khóa** hầu hết hướng này trong invariants 6, 7, 12–15 và vòng GROUND→…→LEARN ở 01.
Gap chính nằm ở **làm đủ harness trong runtime Ask + HITL + observability**, không phải viết lại chiến lược sản phẩm.

## 1. Nguồn đã đọc (mẫu)

| Nguồn trong repo | Vì sao liên quan Data Agent |
| --- | --- |
| `Cursor/cursor.md` + cloud-agents | Mode selection, tool discipline, subagent types, verification trước “done” |
| `Anthropic/claude-code/` (main + `agents/Explore.md`, `Plan.md`, `general-purpose.md`) | Specialist cards, read-only plan/explore, blast radius, typed auto-memory, prompt-injection trên tool result |
| `OpenAI/Codex/gpt-5.6.md` | Commentary vs final, autonomy theo loại request, skills progressive disclosure, destructive-action gate |
| `Perplexity/deep-research.md` | Skill activation bắt buộc, clarifying rules, parallel tool calls, citation / evidence workflow |
| `Anthropic/research_instructions.md` | Deep research mode: clarify ≤3 rồi launch; không hỏi lại; không gọi tool lệch mode |

## 2. Nguyên tắc vận hành (paraphrase) → chỗ khớp V3

| # | Nguyên tắc từ agent hàng đầu | Đã có trong V3 / code? | Gap vận hành |
| --- | --- | --- | --- |
| P1 | Chọn **mode/skill** trước khi gọi tool (Plan vs Agent; `load_skill` trước research) | 01 mục 2 mức tự chủ theo vai; Ask có `mode=analyze\|ask\|answer` | Deep analysis chưa là skill hạng nhất với methodology riêng; chưa “load skill” trước nhánh nặng |
| P2 | **Specialist** có whenToUse + tool cấm/cho phép + chỉ trả summary | `specialists.py` (numbers/documents); 01 §3.2 supervisor/sub-agent | Thiếu metadata whenToUse cho planner; chưa đủ chuyên gia (viz, critic, python) như bảng 00 §6 |
| P3 | Clarify tối đa 1–3, chỉ khi ambiguity **đổi hướng**; else assume + ghi chú | Planner rule 9 (`mode=ask`); 01: C-level hỏi lại tối đa một câu | Chưa có policy tập trung (số câu, khi nào skip, không hỏi lần 2) dùng chung Ask + deep research |
| P4 | Không khẳng định số/fact trước observation; instruction boundary | Bất biến 6–7; `BOUNDARY` + `REPORTER` facts `{{Fn}}` | Cần enforce ở harness (chặn block có số ngoài facts), không chỉ nhờ prompt |
| P5 | Đọc/local OK; destructive / shared / external → confirm | Bất biến 12; 01 §2.2 bảng rủi ro hành động | Cần ma trận HITL **runtime** (capability × risk → auto\|approve\|deny) thống nhất API + agent |
| P6 | Progress stream ≠ final answer | Ask stream UI một phần | Contract sự kiện: `progress` / `plan` / `tool` / `final` / `clarify`; operator view thấy DAG |
| P7 | Verify trước “xong”; checker ra chỉ thị | 01 bước VERIFY + MATERIALIZE; `AUDIT_SQL`, `REVIEW_ANSWER` | VERIFY/MATERIALIZE chưa luôn bắt buộc trên mọi đường; output contract còn mỏng |
| P8 | Memory typed; correction bền qua duyệt | T3 học từ sử dụng (00 §6); correction memory trong chiến lược | Chưa có schema memory kiểu user/feedback/project cho Ask; steward gate cho rule bền |
| P9 | Prompt-injection: tool/doc là data | `BOUNDARY` trong prompts | Mở rộng: flag khi observation có dấu hiệu ghi đè role; audit |
| P10 | Parallel tool / fan-out khi độc lập | Hybrid investigate_numbers ∥ documents | Planner nên song song hoá thêm các nhánh SOFT_DEP độc lập |

**Không đem vào product (anti-goals):**

- Copy giọng “coding agent IDE” vào Ask nghiệp vụ.
- Mở L5 / tự sửa production / tự duyệt skill.
- Thay Context Layer bằng “search web mặc định” kiểu Perplexity consumer.
- Prompt dài thay harness: 01 đã nói harness quan trọng ngang model.

## 3. Tám thay đổi vận hành ưu tiên (đợt tiếp theo)

Thứ tự = impact × khớp V3 × chi phí. Mỗi mục: *làm gì*, *không làm gì*, *bằng chứng xong*.

### OP-1 — Clarify policy tập trung (nhỏ, nhanh)

- **Làm:** Một module policy dùng chung planner + deep mode: tối đa N câu (C-level N=1, analyst N=3); hỏi chỉ khi ≥2 interpretation đổi metric/kỳ/phạm vi; sau một vòng clarify → không hỏi lại, chạy với giả định đã ghi.
- **Không:** Clarify cho lịch sự hoặc “bạn muốn sâu hơn không?”.
- **Xong khi:** Unit + vài case agent-eval: câu rõ → `analyze`; câu mơ hồ metric → đúng 1 clarify; sau trả lời → không clarify lần 2.

### OP-2 — Specialist cards cho planner (whenToUse / tools)

- **Làm:** Mô tả chuyên gia giống Claude Code agent frontmatter: `whenToUse`, `tool_names`, `disallowed`; planner chỉ thấy card + summary, không thấy transcript trong.
- **Không:** Nested specialist / peer chat (đã out of scope trong `specialists.py`).
- **Xong khi:** Hybrid và “chỉ số / chỉ tài liệu” giữ đúng như probe hiện có; thêm card không phá allowlist.

### OP-3 — Ma trận blast-radius → HITL

- **Làm:** Bảng capability × rủi ro (đọc DS, exploratory SQL, gửi kênh ngoài, ghi context, chi phí > budget) → `auto | hitl | forbid`; API + worker cùng nguồn sự thật (catalogue / policy).
- **Không:** Agent tự nâng quyền sau khi user duyệt một lần (Codex: approve một lần ≠ mọi ngữ cảnh).
- **Xong khi:** Screen-audit / e2e: viewer không ghi; gửi ngoài tạo HITL; steward-only paths 403 có `access` block.

### OP-4 — Streaming contract (commentary / final)

- **Làm:** Chuẩn sự kiện Ask: tiến độ ngắn (đang lập kế hoạch, đang chạy bước s2…), clarify ở kênh dừng chờ user, final tự chứa (headline + blocks + trust label). UI có thể gom progress.
- **Không:** Đổ tên tool thô cho end-user (Cursor/Perplexity: nói việc, không nói API).
- **Xong khi:** Một turn Ask luôn có final tự chứa; operator (analyst) bật được DAG/tool log.

### OP-5 — VERIFY + output contract bắt buộc trước PRESENT

- **Làm:** Sau EXECUTE: checker tất định (schema, đơn vị, kỳ, quyền, đủ evidence); optional `REVIEW_ANSWER` / `AUDIT_SQL` theo trust path; MATERIALIZE theo output contract (cột/độ chính xác) — đúng 01 §3.1 bước 5–7.
- **Không:** Judge FAIL → replan mù không phân loại nguyên nhân (01 đã cấm hướng này).
- **Xong khi:** Case “tính đúng nộp sai cột” bị chặn hoặc gắn INSUFFICIENT; trust label khớp đường GOVERNED vs EXPLORATORY.

### OP-6 — Deep analysis = skill + mode (không phải chat dài hơn)

- **Làm:** Mode/skill “deep”: nạp methodology (đa vòng evidence, parallel fan-out, báo cáo tái lập) trước tool nặng — tương tự Perplexity `load_skill` + Anthropic research launch; sản phẩm vẫn query-in-place + nhãn tin cậy.
- **Không:** Deep mode được web-search tuỳ tiện thay semantic/SQL trong Data Space.
- **Xong khi:** Một eval set deep (báo cáo nhiều phần) có artifact Workspace versioned; chi phí/latency budget rõ.

### OP-7 — Typed correction memory + steward gate

- **Làm:** Memory ngắn hạn phiên + đề xuất bền (`feedback` / synonym / verified query) luôn qua inbox duyệt (T3, bất biến 12). Ghi *why* + *how to apply* để tránh rule mù.
- **Không:** Tự áp metric/SQL “vì user sửa 1 lần trong chat”.
- **Xong khi:** Sửa cùng lỗi 3 lần → proposal trong needs-you; không đổi semantic cho đến khi approve.

### OP-8 — Operator observability (harness nhìn được)

- **Làm:** Mỗi run: plan DAG, step status, tool args đã redact, tokens/cost, HITL links, trust label — đủ để steward/analyst “giám sát L3” (01 §0).
- **Không:** Log raw rows PII ra UI mặc định.
- **Xong khi:** Màn run/debug (hoặc export) trả lời “bước nào fail, vì sao, đã replan chưa” không cần đọc pod log.

## 4. Bản đồ sang backlog

Gợi ý gắn `docs/v3/04-backlog.md` (khi chốt đợt 2):

| OP | Gần hạng mục sẵn có |
| --- | --- |
| OP-1, OP-2, OP-5 | Agent core Ask / Plan IR / verifier |
| OP-3 | HITL + catalogue capabilities |
| OP-4, OP-8 | UI Ask stream + Workspace run view |
| OP-6 | Deep analysis (Q7) |
| OP-7 | Correction memory / Review Inbox |

## 5. Việc không làm ở đợt 1 (đã giữ lời)

- Đợt 1 chỉ thiết kế (09/11/2026): không đổi runtime trong commit doc.
- Không merge wording từ file leaks vào `agentic/prompts.py` (prompt product vẫn tự viết).

## 6. Đợt 2 — code xong (thứ tự 1 → 3 → 5 → 4 → 2 → 8 → 6 → 7)

| OP | Bằng chứng |
| --- | --- |
| OP-1 | `clarify_policy.py` + `_bind_clarify` / `prior_clarify_rounds`; PLANNER rule 9 |
| OP-3 | `blast_radius.py` → `runs.needs_checkpoint`; suggest_only luôn HITL; write_source forbid |
| OP-5 | `present_gate.py` VERIFY + chặn draft sai cột |
| OP-4 | `stream_contract.py`; final tự chứa; FE vẽ trước GET |
| OP-2 | `Specialist.when_to_use` + `planner_cards()` trong PLANNER |
| OP-8 | `operator_view` → `plan.steps.operator`; FE `StepTimeline` hiện `operatorWhy` |
| OP-6 | `deep_skill`; `purpose=deep` (Ask `/report`) hoặc `payload.depth=deep` |
| OP-7 | `correction_gate` → inbox `feedback_fix`; `proposals.decided` ack, không ghi semantic |

Audience mặc định Ask = `analyst` (3 vòng / 5 options); `event.payload.audience=executive` → 1 vòng / 3 options.

**Còn ngoài đợt này (cố ý):** eval set deep versioned (OP-6 “xong khi” đầy đủ); màn operator riêng ngoài timeline Ask; apply synonym/metric từ feedback_fix vẫn là proposal tách.
