# DS-AW · baseline hiệu chỉnh độ tin cậy (EV-19)

Đo ngày 19/11/2026 trên cluster dev, mã của nhánh EV-19 trên `08a72d97` (chép vào một pod tạm cùng image và env với
worker-agent, để không chạy cạnh consumer). Mô hình là alias `text_to_sql` mà LiteLLM của cluster đang phục vụ. Bộ eval:
**Câu hỏi AdventureWorks** (`deploy/docker/eval/ds-aw-cases.yaml`, 61 item, đáp án chưa qua hai người giải mù). Cách
tính: [CONFIDENCE_SPEC.md](../../blueprint/CONFIDENCE_SPEC.md) §2–3. Mọi chỉ số "đã hiệu chỉnh" tính bằng cross-fit 5 fold.

```
make sqlgen-ablation ABLATION_ORG=… ABLATION_AS=… ABLATION_ARGS="--variants all --json" > ablation.json
make agent-eval AGENT_EVAL_ORG=… AGENT_EVAL_AS=… AGENT_EVAL_ARGS="--engines governed --json" > agent-eval.json
make calibrate CALIBRATE_PRODUCER=sqlgen_vote CALIBRATE_FROM=ablation.json
make calibrate CALIBRATE_PRODUCER=understanding CALIBRATE_FROM=agent-eval.json
```

## 1. Vote SQL khám phá (`sqlgen_vote`)

35 item số (một giá trị) đi đường khám phá: 2 không nối được bảng, 3 có mọi ứng viên hỏng. Còn **30 lượt có nhóm thắng,
12 đúng (40 %)**.

| Share của vote (`k/6`) | Lượt | Nhóm thắng đúng | Độ đúng thô |
| --- | --- | --- | --- |
| 6/6 | 16 | 11 | 68,8 % |
| 5/6 | 4 | 0 | 0 % |
| 4/6 | 5 | 0 | 0 % |
| 3/6 | 3 | 1 | 33,3 % |
| 2/6 | 2 | 0 | 0 % |

| | ECE (95 % CI) | Brier | AURC |
| --- | --- | --- | --- |
| điểm thô coi như xác suất | 0,428 [0,294; 0,583] | 0,366 | 0,417 |
| đã hiệu chỉnh (oof) | 0,154 [0,117; 0,306] | 0,188 | 0,497 |

| Ngưỡng trong mã hôm nay | Trả lời | Coverage | Sai | Rủi ro | Cận trên 95 % |
| --- | --- | --- | --- | --- | --- |
| `answer` k ≥ 4 | 25 | 83,3 % | 14 | **56,0 %** | 70,9 % |
| `review` k = 3 | 3 | 10,0 % | 2 | 66,7 % | 92,2 % |

Đọc kết quả:

1. **`k/6` không phải xác suất.** ECE 0,43: 4/6 và 5/6 nghe như "khá chắc" nhưng 0/9 lượt đúng. Chỉ 6/6 tách được, và
   cả ở đó vẫn có 5 lượt sáu ứng viên cùng sai. Ở ngưỡng trả lời hôm nay, hơn một nửa câu đã trả lời là sai. Nhãn
   "Khám phá" là đúng, nhưng con số 4/6 trên đồng hồ R1-14 đang hứa nhiều hơn mức nó đạt.
2. **Không ngưỡng nào giữ được rủi ro 5 %** trên dữ liệu này (cả 20 %, cho band review). Hiệu chỉnh hạ ECE xuống 0,15
   nhưng chỉ còn hai mức (6/6, và mọi share khác gộp lại ở 0,25). AURC của bản hiệu chỉnh tệ hơn điểm thô (0,50 so với
   0,42), vì PAVA gộp các share thấp thành một khối nên mất thứ hạng giữa chúng. Với 30 lượt, đó là giới hạn của dữ liệu,
   không phải của phương pháp.
3. **Điểm nghẽn là khâu sinh, không phải khâu vote.** Từng ứng viên đúng 36–39 % (skeleton 24/66, examples 26/66, divide
   26/66). Chỉ 14/35 item có ít nhất một ứng viên đúng, và vote chọn sai khi đã có ứng viên đúng chỉ 2 lần.

**Hệ quả cho AE-23.** Cả ba cách viết đều dưới 50 %, nên `w_m = logit(a_m)` âm với cả ba và bị kẹp về cùng một giá trị.
Vote có trọng số khi đó thoái hoá thành vote đa số theo cách viết, và trên DS-AW sửa được tối đa 2 item. Trước khi làm AE-23
cần đo lại trên TLC (EV-05). Trọng số cũng nên dựa trên độ đúng *có điều kiện* (khi cách viết đó đồng thuận với nhóm thắng),
không dựa trên độ đúng trần của từng ứng viên.

## 2. Đọc hiểu câu hỏi (`understanding`)

Đường governed trên 61 item (49 qua). Có **46 lượt** vừa có `expected.metric` vừa đã được đọc thành một metric; 45 đúng
(97,8 %).

| Điểm thô | Lượt | Đúng |
| --- | --- | --- |
| 1,0 (tên, mã) | 44 | 44 |
| 0,9 (synonym) | 2 | 1 |
| 0,8 (model reader / analyst) | 0 | — |

| | ECE (95 % CI) | Brier | AURC |
| --- | --- | --- | --- |
| điểm thô coi như xác suất | 0,017 [0; 0,059] | 0,018 | 0,001 |
| đã hiệu chỉnh (oof) | 0,019 [0,005; 0,067] | 0,022 | 0,001 |

Ngưỡng hôm nay (`proceed` khi ≥ 0,8): trả lời 46/46, sai 1 (2,2 %, cận trên 9,2 %).

Đọc kết quả:

1. **Trên DS-AW, điểm của rule pass đã gần như là xác suất** (ECE 0,017). Một lần đọc sai duy nhất đến từ synonym.
2. **Điểm 0,8 chưa có dữ liệu.** Không lượt nào ở đây đi qua model reader hay analyst, nên chưa thể nói gì về mức trần 0,8
   mà `llm_reader` và `analyst` gán. Đó cũng là chỗ đáng ngờ nhất: một cách đọc của model luôn vừa đủ qua `PROCEED_AT`.
3. **Chưa đủ bằng chứng để chứng nhận rủi ro 3 %.** Kể cả 44 lượt 1,0 không sai lần nào, cận trên Wilson vẫn khoảng 6 %.
   Cần khoảng 90 lượt liền không sai mới xuống dưới 3 %. Chỉ đặt được ngưỡng review (rủi ro ≤ 20 %).

## 3. Kết luận cho AE-24

- `sqlgen_vote` cần hiệu chỉnh trước khi đồng hồ R1-14 hay router dựa vào nó. Hằng số 4/6 hiện cho rủi ro khoảng 56 %.
- `understanding` cần các câu mà rule pass không đọc được (bộ TLC, EV-05, có từ ngữ khác tên metric) thì mới đo được mức
  0,8.
- Với 30 và 46 lượt, mọi khoảng tin cậy đều rộng. Fit map thật chờ tier golden (EV-09) và đo lại hằng đêm (EV-13).
