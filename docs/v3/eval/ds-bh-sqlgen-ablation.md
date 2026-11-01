# DS-BH · ablation của chuyên gia SQL (R1-09)

Đo ngày 01/11/2026 trên cluster dev (`make sqlgen-ablation`, `worker_agent/sqlgen/ablation.py`), mô hình
`text_to_sql` của LiteLLM, bộ eval **Câu hỏi pilot Bán hàng** (`deploy/docker/eval/ds-bh-pilot-cases.yaml`):
101 câu số liệu, mỗi câu một con số, chạy qua **đường SQL khám phá** (đường dự phòng khi lớp ngữ nghĩa
không trả lời được). Một biến thể đúng khi bỏ phiếu cho trả lời (≥ 4/6, hoặc ≥ 3/6 kèm cảnh báo, tính theo tỷ
lệ) và con số lệch ≤ 0,5 % đáp án. Đáp án còn là bản nháp, chưa qua hai người giải mù.

| Biến thể | Trả lời | Đúng | Đúng / tổng | Đúng khi đã trả lời | Tin cậy câu đúng | Tin cậy câu sai |
| --- | --- | --- | --- | --- | --- | --- |
| cả ba cách (production) | 69 | 53 | 52,5 % | 76,8 % | 0,89 | 0,64 |
| chỉ skeleton | 70 | 52 | 51,5 % | 74,3 % | 0,98 | 0,86 |
| chỉ examples | 70 | 60 | 59,4 % | 85,7 % | 0,97 | 0,80 |
| chỉ divide | 70 | 49 | 48,5 % | 70,0 % | 0,99 | 0,93 |

Đọc kết quả:

1. **31/101 câu không nối được bảng** (`sqlgen/schema.link`): khái niệm dẫn xuất không trùng tên bảng hay cột —
   biên lợi nhuận gộp, lãi gộp, tồn kho, công nợ, số đơn, giá trị đơn bình quân, vòng quay, số ngày thu tiền,
   doanh thu đã chốt. Nối theo mô tả bảng và theo tên chỉ số của lớp ngữ nghĩa là việc kế.
2. **16 câu sai mà bỏ phiếu vẫn trả lời** (thường 4/6): biên lợi nhuận trả về lãi gộp, tỷ lệ giá vốn ra 0,69
   thay vì 69 %, giá trị đơn bình quân chia theo dòng thay vì theo đơn, doanh thu bình quân ngày chia theo dòng.
   Đó là lý do câu khám phá mang nhãn “Khám phá” và lớp ngữ nghĩa (các chỉ số này đều đã chứng nhận) trả lời
   trước; với DS-BH, đường này chỉ đến khi câu hỏi không gọi tên chỉ số nào.
3. **Bỏ phiếu ba cách chưa hơn một cách**: “examples” (theo 34 truy vấn đã kiểm, R1-08) cho độ chính xác cao nhất;
   ba cách cùng bỏ phiếu kéo xuống vì “divide” và “skeleton” đồng thuận trên cùng một cách hiểu sai. Độ tin cậy có
   tách được đúng / sai (0,89 / 0,64 ở production) nhưng chưa đủ để chặn câu sai ở ngưỡng 4/6.

Đo lại khi bộ câu đã qua hai người giải mù, sau khi sửa nối bảng, và khi đổi mô hình hay cách bỏ phiếu:
`make sqlgen-ablation ABLATION_ORG=… ABLATION_SPACE=DS-BH ABLATION_AS=… ABLATION_SET="Câu hỏi pilot Bán hàng"`.
