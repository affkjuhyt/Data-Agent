# CONFIDENCE_SPEC — Độ tin cậy hiệu chỉnh và vote theo cách viết (F-31)

> Ngày 19/11/2026, trên HEAD `79ff6e18`. Spec bổ sung cho [ASK_ENGINE_SPEC.md](ASK_ENGINE_SPEC.md) (router F-04, SQL khám
> phá §4.4) và [DATASET_AND_EVALUATION.md](DATASET_AND_EVALUATION.md) (scorecard §6.2). Task: **EV-19, AE-23, AE-24**
> (§8). Mọi ngưỡng ở đây là **đề xuất, chưa đo**; chốt theo quy trình rebase của EV-14.

## 0. Vấn đề

Mỗi bộ phận tự đặt "độ tin cậy" trên một thang riêng, và các quyết định chuyển đường (trả lời, hỏi lại, đưa duyệt, giao
cho đường khác) dùng hằng số đoán tay trên những thang đó:

| Nơi sinh | Điểm thô | Quyết định dựa trên nó | Vị trí |
| --- | --- | --- | --- |
| Resolver (rule pass) | 1,0 tên/mã; 0,9 synonym | `proceed` khi metric duy nhất và điểm ≥ `PROCEED_AT = 0,8` | `understand/resolver.py:30,151,256`; `Understanding.confidence` = min chip metric/filter (`:129`) |
| LLM reader, analyst pass | trần cứng `MODEL_CONFIDENCE = 0,8` | cùng ngưỡng 0,8: một cách đọc của model luôn vừa đủ qua | `understand/llm_reader.py:37,176`; `understand/analyst.py:59` |
| Vote SQL khám phá | k/6 (cỡ nhóm kết quả lớn nhất) | `answer` k ≥ 4, `review` k = 3, `ask` k ≤ 2 | `sqlgen/vote.py:16-18,46`; `sqlgen/run.py:117-125` |
| Đồng hồ độ tin cậy (R1-14) | `agreeing / written` | hiển thị; agentic lấy min các dataset | `answer/verification.py:86-89`; `agentic/engine.py:1848` |
| `qa.plan.confidence` | min điểm các plan của lượt | ghi chú cột "Dưới 0,8 thì hỏi lại" | `models/qa.py:155`; `runs.py:919` |

Hệ quả:

1. **Không so được giữa các nguồn.** "0,8" của LLM reader và "4/6" của vote không cùng nghĩa, nên router (AE-01), planner
   và specialist không có thước chung để chọn giữa governed, khám phá, hỏi lại hay giao đi.
2. **Điểm không phải xác suất.** Không ai đo được "0,9" (synonym) đúng bao nhiêu phần trăm. Ablation đã in confidence trung
   bình của câu đúng và câu sai (`sqlgen/ablation.py:67-68,93-104`) nhưng chưa có chỉ số hiệu chỉnh nào.
3. **Vote đếm trùng.** Sáu ứng viên là 3 cách viết × 2 nhiệt độ (`sqlgen/generate.py:24-25`). Hai mẫu của cùng một cách
   viết tương quan mạnh, nên "4/6" có thể chỉ là hai ý kiến độc lập (hai cách viết, mỗi cách 2 mẫu).

## 1. Phạm vi

- **Trong phạm vi:** hai bộ sinh điểm đang quyết định đường đi: *understanding* (resolver + LLM reader + analyst) và
  *sqlgen_vote*. Một thước chung `CalibratedConfidence` được mang trên dataset của Workspace để planner và specialist đọc
  cùng một thang. Vote theo cách viết.
- **Ngoài phạm vi:** confidence trường trích tài liệu (`documents/extraction.py`, đã có ngưỡng review riêng); điểm truy hồi
  (thanh relevance của `documents/retrieve.py`); khoảng dự báo (đã conformal ở `libs/forecast_fit`); verified fuzzy match
  (AE-12): thêm làm bộ sinh thứ ba khi AE-12 có 40 case của nó, không làm trong spec này.
- **Không làm:** hỏi model tự báo độ tin cậy (verbalized confidence hiệu chỉnh kém và không tái lập). Không đổi UI: đồng hồ
  R1-14 vẫn hiện `agreeing/written`. Việc có cho người dùng thấy xác suất hay không là quyết định sản phẩm riêng.
- **Bất biến giữ nguyên:** độ tin cậy chỉ chọn đường đi; nó không bao giờ nâng `trust_code` (ARCHITECTURE §9.5) và không
  thay `present_gate`, `audit_rules` hay quyền (bất biến 13). Một câu khám phá có p = 0,99 vẫn mang nhãn EXPLORATORY.

## 2. Định nghĩa

Với bộ sinh `P` và đặc trưng thô `x` của một lượt, **độ tin cậy hiệu chỉnh** là

```
p̂_P(x) = P(đúng | x)      "đúng" định nghĩa theo bộ sinh, §3
```

ước lượng từ các lượt eval có nhãn. Ta nói `P` hiệu chỉnh tốt khi trong các lượt có `p̂ ≈ 0,8` thì khoảng 80 % là đúng.
Quyết định dùng **cận dưới** `p_low` (một phía, 95 %), không dùng `p̂`, để một ô ít mẫu không bao giờ được tin hơn mức dữ
liệu cho phép.

### 2.1 Đặc trưng thô `x`

| Bộ sinh | `x` | Kiểu |
| --- | --- | --- |
| `understanding` | điểm thô `Understanding.confidence` như `qa.plan.confidence` lưu: 1,0 tên/mã, 0,9 synonym, 0,8 model reader hoặc analyst (cả hai chặn ở 0,8), giá trị khác khi một chip filter khớp gần đúng | rời rạc, mỗi giá trị một bin |
| `sqlgen_vote` | EV-19 (baseline): share `k/total` của vote hiện tại. Từ AE-23: `margin` = (S₁ − S₂) / W của vote theo cách viết (§4) | liên tục trên [0, 1], chia bin |

Điểm hiện tại của resolver (1,0 / 0,9 / 0,8) chỉ là nhãn của nguồn đọc, nên hiệu chỉnh nó là tra bảng theo lớp. Hàm liên tục
kiểu Platt không có ý nghĩa trên ba giá trị. Model reader và analyst hiện không phân biệt được từ dữ liệu đã lưu (cùng 0,8),
và một lượt đã `proceed` luôn có đúng một metric ở điểm cao nhất. Vì vậy EV-19 dùng chính điểm thô; việc tách nguồn đọc thuộc
AE-24, và chỉ làm khi dữ liệu cho thấy hai bộ đọc đúng khác nhau.

### 2.2 Ánh xạ hiệu chỉnh (`CalibrationMap`)

Fit offline, áp dụng lúc chạy bằng tra bảng (worker không cần numpy/scipy):

```
1. Chia bin: lớp rời rạc giữ nguyên; x liên tục chia equal-mass, B = 5 khi N < 200, 10 khi N ≥ 200.
2. Prior Beta theo empirical Bayes: a₀ = n₀·ā, b₀ = n₀·(1 − ā), với ā = độ đúng chung của bộ sinh, n₀ = 4.
3. Mỗi bin b có n_b lượt, c_b lượt đúng:  p̂_b = (c_b + a₀) / (n_b + a₀ + b₀).
4. Ép đơn điệu không giảm theo x bằng PAVA (trọng số n_b + n₀), cả với lớp rời rạc: điểm thô cao hơn không được tin
   ít hơn. Báo cáo vẫn in độ đúng thô từng bin, để thấy bin nào đã bị gộp.
5. p_low = cận dưới Wilson (z = 1,645) của khối đã gộp: (p̂_khối · W_khối, W_khối), W_khối = Σ (n_b + n₀).
```

Một giá trị `x` chưa gặp (lớp mới, ngoài khoảng bin) lấy bin gần nhất về phía yếu hơn, tức là bảo thủ. Map là một tệp JSON
có version:

```json
{
  "producer": "sqlgen_vote",
  "version": "2026-12-xx.1",
  "source_run": "<eval_run_id hoặc tên tệp ablation --json>",
  "commit": "<sha>", "snapshot": "DS-AW@… | TLC-2024Q1",
  "prior": {"a0": 3.1, "b0": 0.9},
  "bins": [{"lo": 0.0, "hi": 0.34, "n": 21, "correct": 9, "p": 0.45, "p_low": 0.29}, "…"],
  "thresholds": {"answer": 0.80, "review": 0.55},
  "metrics": {"ece": 0.07, "ece_ci95": [0.03, 0.14], "brier": 0.15, "aurc": 0.09, "n": 112}
}
```

Các số trong ví dụ là hình dạng, không phải số đo.

### 2.3 Chỉ số

```
ECE   = Σ_b (n_b / N) · | acc_b − mean(p̂ trong b) |     equal-mass, B như §2.2; báo khoảng tin cậy 95 % bootstrap (1 000 lần, seed cố định)
Brier = mean( (p̂ − y)² )
Risk–coverage: xếp các lượt theo p̂ giảm dần; với mỗi ngưỡng τ:
      coverage(τ) = |{p̂ ≥ τ}| / N,   selective_risk(τ) = sai trong {p̂ ≥ τ} / |{p̂ ≥ τ}|
AURC  = diện tích dưới đường selective_risk theo coverage (càng nhỏ càng tốt)
```

Fit và đo trên cùng dữ liệu thì luôn đẹp quá mức. Mọi chỉ số báo cáo tính bằng **cross-fitting 5 fold** (fit trên 4, đo trên
1, gộp). Map cuối cùng fit trên toàn bộ dữ liệu.

### 2.4 Chọn ngưỡng từ rủi ro chấp nhận được

Ngưỡng không còn là hằng số trong mã. Nó là ngưỡng thấp nhất giữ rủi ro trong mức sản phẩm chấp nhận:

```
τ_answer = min τ sao cho Wilson_upper( selective_risk trên {p̂ ≥ τ} ) ≤ r_answer
τ_review = min τ sao cho Wilson_upper( selective_risk trên {τ ≤ p̂ < τ_answer} ) ≤ r_review
p_low ≥ τ_answer → answer;   τ_review ≤ p_low < τ_answer → review / xác nhận;   còn lại → ask (hỏi lại)
```

Mức rủi ro đề xuất (provisional): `r_answer = 0,05`, `r_review = 0,20` cho `sqlgen_vote`; `understanding` dùng
`r_answer = 0,03`, vì đọc sai metric thì sai cả câu trả lời mà người đọc không thấy. Đây là quyết định sản phẩm; rebase như
`thresholds.json` (EV-14).

## 3. Nhãn "đúng" cho từng bộ sinh

| Bộ sinh | `y = 1` khi | Nguồn dữ liệu |
| --- | --- | --- |
| `understanding` | metric đọc ra = `expected.metric` của item | `make agent-eval --engines governed --json` (`expected_metric`, `understood`, `confidence` mỗi item); lượt eval chạy đêm ghi `actual.confidence.understanding` cạnh `eval_result.understanding_ok`. Chỉ item có `expected.metric` và đã được đọc thành một metric |
| `sqlgen_vote` | giá trị của **nhóm thắng** lệch ≤ 0,5 % (`Outcome.winner_correct`), bất kể vote quyết gì | `make sqlgen-ablation --variants all --json`; mỗi item số của eval set đều đi đường khám phá, nên có nhãn cho cả câu mà governed sẽ trả lời. Item không có nhóm thắng (mọi ứng viên hỏng) không vào hiệu chỉnh |

Nhãn của vote là "nhóm thắng có đúng không", không phải `Outcome.correct` ("đã trả lời và đúng"). Lý do: `correct` coi mọi
lượt bị quyết `ask` là sai, nên các share thấp sẽ bị gán sai do chính cách đo chứ không phải do dữ liệu. Ablation `--json`
cũng ghi mỗi ứng viên: cách viết, nhóm kết quả, giá trị riêng và đúng/sai, để AE-23 đếm lại vote offline.

Rủi ro dữ liệu (đã biết): DS-AW chỉ có 61 item, và governed đúng 20/20 câu đầu, nên `understanding` có rất ít lượt sai. Khi
đó map bị prior chi phối và `p_low` thấp. Đó là hành vi đúng: thiếu bằng chứng thì không nới ngưỡng. Fit thật cần 60 case
TLC (EV-05) qua tier golden (EV-09).

## 4. Vote theo cách viết (AE-23)

Thay đếm ứng viên bằng đếm cách viết, có trọng số theo độ đúng riêng của từng cách:

```
Với nhóm kết quả g (cùng signature, vote.signature) và cách viết m ∈ {skeleton, examples, divide}:
  f_{m,g} = (số mẫu của m chạy được và rơi vào g) / (số mẫu của m)        ∈ {0, ½, 1}
  w_m     = logit( a_m ),  a_m = (đúng_m + 1) / (item_m + 2)              độ đúng của m khi viết một mình (ablation variant m)
  S_g     = Σ_m w_m · f_{m,g}
  W       = Σ_m w_m                                                       mọi cách viết, kể cả cách thất bại hết
winner = argmax S_g (hoà: chỉ số ứng viên nhỏ nhất, như hiện tại);   margin = (S₁ − S₂) / W    (S₂ = 0 khi chỉ một nhóm)
```

- Mỗi cách viết góp tối đa một phiếu cho một nhóm. Hai mẫu cùng cách viết rơi vào hai nhóm thì mỗi nhóm nhận nửa phiếu.
  Đây là cách khử tương quan đơn giản nhất mà không cần ước lượng hiệp phương sai.
- `w_m = logit(a_m)` là trọng số của vote đa số tối ưu khi các nguồn độc lập có độ đúng `a_m` (dạng một tham số của
  Dawid–Skene). Có `a_m ≤ 0,5` thì `w_m ≤ 0`; khi đó kẹp `w_m` về `0,05`, để một cách viết kém vẫn góp phiếu nhỏ chứ không
  phiếu âm. Thiếu ablation cho Data Space thì dùng trọng số toàn cục; không có cả hai thì `w_m = 1`, tức vote đa số theo
  cách viết.
- **Đo ở EV-19 (19/11/2026, DS-AW):** từng ứng viên đúng 36–39 % với cả ba cách, nên cả ba `w_m` bị kẹp và vote có trọng
  số thoái hoá thành vote đa số theo cách viết. Chỉ 14/35 item có ít nhất một ứng viên đúng; vote chọn sai khi đã có ứng
  viên đúng chỉ 2 lần ([báo cáo](../v3/eval/2026-11-19-calibration-baseline.md)). Trước khi làm AE-23: đo lại trên TLC, và
  cân nhắc đặt `a_m` theo độ đúng *có điều kiện* (khi cách viết m đồng thuận với nhóm thắng) thay vì độ đúng trần của ứng
  viên.
- Cách `examples` không có example nào của Data Space thì thoái hoá thành chỉ dẫn chung (`generate.py:99`). Trọng số đo
  trên Data Space đó tự phản ánh điều này.
- `Vote` giữ `confidence = k/total` cho đồng hồ R1-14 (UI không đổi) và thêm `margin`, `support: dict[str, float]`.
- Ablation và `explore` ghi cho mỗi ứng viên `method` và chỉ số nhóm. Nhờ vậy vote mới **chấm lại offline** trên đúng các
  ứng viên đã sinh, không gọi model lần nữa, và so sánh hai cách vote là tất định.

## 5. Một thước chung cho các agent (AE-24)

```python
# backend/libs/calibration/__init__.py — stdlib, không I/O ngoài đọc tệp map
@dataclass(frozen=True)
class CalibratedConfidence:
    producer: Literal["understanding", "sqlgen_vote"]
    raw: dict[str, Any]          # x của §2.1, để tái lập
    p: float                     # p̂
    p_low: float                 # cận dưới; mọi quyết định đọc giá trị này
    map_version: str

def calibrate(producer: str, x: Any) -> CalibratedConfidence: ...   # tra map; thiếu map → p = p_low = điểm thô cũ, map_version "identity"
def decide(c: CalibratedConfidence) -> Literal["answer", "review", "ask"]: ...
```

Nơi dùng:

| Nơi | Thay đổi |
| --- | --- |
| `understand/resolver.py:256` | `best < PROCEED_AT` → `decide(calibrate("understanding", x)) == "ask"` khi cờ `on`; `PROCEED_AT` giữ cho map `identity` |
| `sqlgen/vote.py`, `sqlgen/answer.py` | `decision` từ `decide(calibrate("sqlgen_vote", margin))` khi cờ `on`; `ANSWER_AT`/`REVIEW_AT` giữ cho map `identity` |
| `agentic/workspace.py:78` | `Dataset.confidence: CalibratedConfidence \| None` cạnh `consensus` |
| `agentic/tools/query.py:1099` | gán `confidence` cho dataset khám phá; observation ghi "độ tin cậy ≈ {p:.0%} (cận dưới {p_low:.0%})", để planner quyết định kiểm thêm hay dừng |
| `agentic/specialists.py` | tóm tắt specialist gửi lead mang `p_low` nhỏ nhất của các dataset nó tạo |
| `agentic/present_gate.py` | khối KPI/chart/table đọc dataset có `p_low < τ_review` → replan hoặc giao đi, không compose |
| `router.py` (AE-01) | `single & complete` đòi `decide(understanding) == "answer"`; `review` → clarify theo `clarify_policy` |
| `qa.plan.steps["confidence"]` | ghi `CalibratedConfidence` (jsonb sẵn có, không schema mới, ADR-59); cột `qa.plan.confidence` giữ điểm thô |

**Gộp nhiều dataset:** độ tin cậy của câu trả lời = `min p_low` của các dataset nó dùng. Lấy min chứ không lấy tích: các
bước của một lượt không độc lập (cùng hiểu sai câu hỏi thì sai cùng nhau). Min cũng là cách `_consensus` và `trust_code`
đang gộp.

**Triển khai từng bước:** cờ `CALIBRATED_DECISIONS` = `off | shadow | on`, mỗi bộ sinh một giá trị, mặc định `shadow`.
Ở `shadow`, hệ thống tính và ghi `CalibratedConfidence` vào trace nhưng vẫn quyết định bằng hằng số cũ. Chỉ chuyển `on`
khi AE-24 đạt tiêu chí trên lượt eval có SHA.

## 6. Công cụ

- `backend/libs/calibration/`: `fit`, `apply`, `wilson`, `pava`, `ece`, `brier`, `risk_coverage`, `aurc`,
  `choose_thresholds`, `cross_fit`, chỉ dùng stdlib. Worker import để `apply`, `model/eval` import để `fit` (qua
  `model/eval/_paths.py`, như các scorer khác: đo bằng mã thật, không cài lại).
- `backend/libs/calibration/maps/{understanding,sqlgen_vote}.json`: map đã fit, commit cùng mã; đổi map là một commit có
  `source_run`.
- `model/eval/calibrate.py` + `make calibrate` (`CALIBRATE_ARGS`: `--producer`, `--from <eval-report.json | ablation.json>`,
  `--write`): in báo cáo §2.3, chỉ ghi map khi có `--write`.
- `eval_runs.run_eval` ghi `actual.confidence = {producer: x}`; ablation `--json` ghi `x` và ứng viên theo §4. Cả hai là
  jsonb sẵn có (`eval.eval_result.actual`), không migration.
- Metrics (PL-05): histogram `ask_confidence_p_low{producer}`, counter `ask_confidence_decision_total{producer,decision,mode}`.
  Ở `shadow` có thêm `…{disagree="true"}` khi quyết định mới khác quyết định cũ, để thấy trước những gì sẽ đổi khi bật `on`.
- Image worker-agent chỉ COPY các thư viện được liệt kê trong `backend/services/worker_agent/Dockerfile`, và
  `libs/calibration` chưa nằm trong đó (EV-19 chưa cần: chỉ `model/eval` dùng nó). AE-24 phải thêm
  `COPY backend/libs/calibration/ ./libs/calibration/` trong cùng commit với dòng import đầu tiên ở worker.

## 7. Test

- `tests/unit/repo/test_calibration.py` (suite `repo`: thư viện chỉ dùng stdlib, không cần image dịch vụ): PAVA cho ra dãy đơn điệu; Wilson khớp bảng tra; ECE = 0 trên dữ liệu hiệu chỉnh
  hoàn hảo dựng sẵn; `choose_thresholds` trên dữ liệu tổng hợp; cross-fit không để rò fold; map `identity` tái tạo hành vi
  cũ (0,8 / 4-3).
- `tests/unit/worker_agent/test_sqlgen_vote.py` (mở rộng): 2 cách × 2 mẫu cùng nhóm thắng 1 cách × 2 mẫu + 2 cách × 1 mẫu
  chia nhóm khi trọng số bằng nhau; nửa phiếu khi một cách tách đôi; `w_m` kẹp; không có ablation → đa số theo cách viết;
  `confidence = k/total` không đổi.
- `tests/unit/worker_agent/test_resolver.py` (mở rộng): cờ `off`/`shadow` cho kết quả y hệt hiện tại trên toàn bộ test cũ.
- `tests/unit/worker_agent/test_present_gate.py` (mở rộng): dataset `p_low < τ_review` không thành KPI.
- Offline: chấm lại vote từ ablation `--json` đã lưu, so AURC hai cách vote (tất định, không gọi model).

## 8. Danh sách task

| ID | Ưu tiên | Mô tả | Thay đổi cần làm | Công nghệ | Phụ thuộc | Vai | Công sức | Tiêu chí chấp nhận | Test | Deliverable | Rollback |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| EV-19 | P1 | Đo hiệu chỉnh: ghi đặc trưng thô và nhãn, `libs/calibration` (chỉ số + fit), `make calibrate` báo cáo; baseline của điểm thô hiện tại | mới `backend/libs/calibration/`, `model/eval/calibrate.py`, `Makefile`; `eval_runs.py` ghi `actual.confidence`; `sqlgen/ablation.py` `--json` ghi `x` và ứng viên (method, nhóm); scorecard DATASET §6.2 | stdlib | — | AI/QA | S | Báo cáo có ECE (+CI), Brier, AURC, đường risk–coverage cho cả hai bộ sinh trên DS-AW, cross-fit 5 fold, ghi `snapshot`/`commit`; baseline ghi vào DATASET §6.2 | `test_calibration.py` | `docs/v3/eval/<date>-calibration-baseline.md` | xoá target; cột jsonb không đổi |
| AE-23 | P1 | Vote theo cách viết có trọng số `logit(a_m)`; `margin`, `support`; `confidence = k/total` giữ cho R1-14 | `sqlgen/vote.py`, `sqlgen/run.py` (truyền method), `sqlgen/ablation.py` (trọng số từ variant đơn, chấm lại offline) | — | EV-19 | AI | S | Chấm lại offline trên cùng ablation DS-AW: AURC ≤ vote k/6, và ở cùng coverage selective_risk không cao hơn; khoảng tin cậy bootstrap được báo kèm | `test_sqlgen_vote.py` (+6) | trọng số toàn cục trong map `sqlgen_vote` | cờ `SQLGEN_VOTE=count` |
| AE-24 | P1 | `CalibratedConfidence` + map + ngưỡng từ rủi ro; nối resolver, vote, Workspace, specialist, present_gate, router; cờ `off\|shadow\|on` | `libs/calibration/maps/*.json`; `understand/resolver.py:256`; `sqlgen/vote.py`, `sqlgen/answer.py`; `agentic/{workspace,specialists,present_gate}.py`, `tools/query.py:1099`; `router.py` | — | EV-19, AE-23, EV-09 (fit thật trên TLC), AE-01 (phần router) | AI/BE | M | Trên lượt golden có SHA, cross-fit: ECE ≤ 0,05 với cận trên CI ≤ 0,10 (provisional); selective_risk ở `τ_answer` ≤ `r_answer` trên fold giữ lại; 20/20 DS-AW governed giữ; `make agent-eval` không giảm `passed`; `shadow` chạy ≥ 7 đêm nightly (EV-13) trước khi `on` | `test_calibration.py`, `test_resolver.py`, `test_present_gate.py`, `test_router.py` (+3) | map v1 cho hai bộ sinh; báo cáo `docs/v3/eval/<date>-calibration.md` | `CALIBRATED_DECISIONS=off` (map `identity`) |
