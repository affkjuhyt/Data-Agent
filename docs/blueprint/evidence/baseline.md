# Baseline đo ngày 12/11/2026 00:04–00:11 (+07), cụm dev local, HEAD 9e503407 (cụm deploy đúng SHA này)

## make eval-retrieval (DS-AW, 156 case, model/eval/cases/retrieval.jsonl)
- Lưu ý: TEI (embedder bge-m3, reranker) KHÔNG reachable trên cụm local: 156 lần "dense document search unavailable", 152 lần rerank unavailable → kết quả thực chất là NỬA TỪ KHOÁ (FTS) dù báo cáo ghi halves keyword/dense/rerank = true (cờ phản ánh cấu hình, không phản ánh thành công → lỗi báo cáo).
- overall: passed 124/156; recall@1 0.7519, recall@3 0.8045, recall@10 0.8045, MRR 0.7744, not_found_accuracy 0.7391, forbidden_rate 0.0
- theo tag: bang r@1 .875/r@10 .9375 (16); bay-so-lieu 1.0 (20); chua-hieu-luc nf .8 (5); da-ngon-ngu 0.0 (3); dien-dat-khac .4286 (7); dinh-nghia r@1 .9 r@10 1.0 (20); docx r@1 .75 r@10 .833; email 1.0 (6); follow-up r@1 .5 r@10 .75; het-hieu-luc nf 1.0 (7); huong-dan-san-pham r@1 .4667 r@10 .5333; injection 1.0 (3); khong-co-dap-an nf .5; khong-dau r@1 .1 (10); mau-thuan r@1 .6 r@10 .8; nhieu-trang r@1 .889 r@10 1.0; pdf .625 (8); sai-chinh-ta 1.0; so-bo r@1 .5 r@10 1.0; tai-lieu-khong-dau .8
- So với RES-12 ghi (DS-AW: recall@1 0,75, MRR 0,77, không-thấy 0,74) → trùng, tức số RES-12 cũng là keyword-only.

## make agent-eval (DS-AW, 20 câu đầu "Câu hỏi AdventureWorks", engines agentic,governed)
engine   items passed any-order agentic handover governed steps tokens seconds
agentic   20    17      17        14      5        1        7.2   22626   16.1
governed  20    20      20        0       0        20       5.0   536     2.3
- agentic fail: "Số đơn Internet năm 2013" → 21.289 (sai số), "Tồn kho cuối tháng 12/2013" → nói chưa có dữ liệu (sai: có), "Giá trị tồn kho cuối tháng 6/2014" → kỳ chưa đầy đủ (từ chối sai).
- governed 20/20 (hai lượt trước: 3/60 khi metric chưa duyệt; 17/20 ngày 03/11 sau khi duyệt). Điểm phụ thuộc semantic model đã duyệt, không phụ thuộc engine.
- Chi phí: agentic 42× token, 7× thời gian so với governed trên cùng câu đơn giản.
- LiteLLM: planner/judge/guardrail… = Qwen3-4B-AWQ (fast); responder/text_to_sql/orchestrator-planner = Qwen3-14B-AWQ (main); một A100 40GB dùng chung.
