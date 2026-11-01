# DS-AW · phiếu giải mù (R1-12)

Người giải: ……………………  Ngày: ……………

Data Space **AdventureWorks** (DS-AW): kho dữ liệu mẫu AdventureWorksDW của Microsoft (nhà sản xuất xe
đạp), nạp bằng `make adventureworks`. Giải từng câu trên dữ liệu, **không xem đáp án trong
`deploy/docker/eval/ds-aw-cases.yaml`** và không bàn với người giải kia. Ghi con số (giữ đơn vị của câu:
USD, %, sản phẩm, đơn, cuộc gọi), nhiều số thì ghi theo thứ tự (theo từng nhóm, từ cao xuống với câu "cao
nhất", kỳ được hỏi trước rồi kỳ so sánh), hoặc ghi **TỪ CHỐI** nếu dữ liệu không trả lời được câu đó, hoặc
**HỎI LẠI** nếu câu thiếu thông tin để trả lời.

Quy ước của mô hình (`deploy/docker/semantic/ds-aw.yaml`): "doanh thu" không nói kênh là cả hai kênh
(Internet và đại lý), không gồm thuế và phí vận chuyển; thời điểm của một đơn là ngày đặt hàng; năm tài
chính bắt đầu ngày 1/7 (năm tài chính 2013 là 1/7/2013 – 30/6/2014); tồn kho đọc ở ngày cuối kỳ có số liệu,
không cộng các ngày; biên lợi nhuận là lãi gộp chia doanh thu trên tổng; số tài chính đọc một kịch bản
(Actual hoặc Budget) và một tài khoản.

Hai phiếu xong thì so với đáp án nháp; câu nào ba bên không khớp thì xem lại câu đó (đề, dữ liệu hoặc đáp án).

| # | Câu hỏi | Kết quả |
| --- | --- | --- |
| aw001 | Doanh thu năm 2013 | |
| aw002 | Doanh thu tháng 6/2013 | |
| aw003 | Doanh thu Internet năm 2013 | |
| aw004 | Doanh thu Internet quý 4/2013 | |
| aw005 | Doanh thu Internet tháng 1/2014 | |
| aw006 | Doanh thu đại lý năm 2012 | |
| aw007 | Lãi gộp Internet năm 2013 | |
| aw008 | Biên lợi nhuận gộp Internet năm 2013 | |
| aw009 | Lãi gộp đại lý năm 2013 | |
| aw010 | Số đơn Internet năm 2013 | |
| aw011 | Giá trị đơn bình quân Internet năm 2013 | |
| aw012 | Chỉ tiêu doanh số năm 2012 | |
| aw013 | Số cuộc gọi tổng đài tháng 5/2014 | |
| aw014 | Sự cố trên 100 cuộc gọi tháng 5/2014 | |
| aw015 | Tồn kho cuối tháng 12/2013 | |
| aw016 | Giá trị tồn kho cuối tháng 6/2014 | |
| aw017 | Doanh thu Internet nhóm hàng Bikes năm 2013 | |
| aw018 | Doanh thu đại lý nhóm hàng Components năm 2013 | |
| aw019 | Doanh thu nhóm hàng Clothing năm 2013 | |
| aw020 | Doanh thu nhóm hàng Components năm 2013 | |
| aw021 | Doanh thu Internet khu vực Europe năm 2013 | |
| aw022 | Doanh thu đại lý loại Warehouse năm 2012 | |
| aw023 | Doanh thu Internet từ khách hàng nữ năm 2013 | |
| aw024 | Doanh thu Internet tiểu nhóm Mountain Bikes tháng 12/2013 | |
| aw025 | Doanh thu Internet của khách hàng ở California năm 2013 | |
| aw026 | Doanh thu đại lý chương trình Volume Discount 11 to 14 năm 2012 | |
| aw027 | Tồn kho nhóm hàng Bikes cuối tháng 12/2013 | |
| aw028 | Doanh thu Internet theo quý năm 2013 | |
| aw029 | Doanh thu đại lý năm 2013 theo từng nhóm hàng | |
| aw030 | Doanh thu Internet theo khu vực năm 2013 | |
| aw031 | Lãi gộp đại lý theo loại đại lý năm 2013 | |
| aw032 | 5 sản phẩm có doanh thu Internet cao nhất năm 2013 | |
| aw033 | 3 đại lý có doanh thu đại lý cao nhất năm 2013 | |
| aw034 | 3 tiểu nhóm sản phẩm có số lượng bán Internet nhiều nhất năm 2013 | |
| aw035 | 2 quốc gia có doanh thu Internet thấp nhất năm 2013 | |
| aw036 | Doanh thu Internet năm 2013 so với cùng kỳ năm trước | |
| aw037 | Doanh thu đại lý quý 3/2013 so với quý trước | |
| aw038 | Biên lợi nhuận gộp Internet năm 2013 so với năm trước | |
| aw039 | Tỷ trọng doanh thu Internet theo khu vực năm 2013 | |
| aw040 | Doanh thu Internet luỹ kế từ đầu năm đến hết tháng 6/2013 | |
| aw041 | Doanh thu đại lý luỹ kế theo quý năm 2012 | |
| aw042 | Doanh thu Internet năm tài chính 2013 | |
| aw043 | Doanh thu đại lý năm tài chính 2012 | |
| aw044 | Doanh thu Internet năm tài chính 2011 | |
| aw045 | Doanh số Trade Sales thực tế năm 2011 | |
| aw046 | Doanh số Trade Sales theo ngân sách năm 2011 | |
| aw047 | Chi phí lương Salaries thực tế năm 2013 | |
| aw048 | Doanh thu theo ngày giao hàng tháng 12/2013 | |
| aw049 | Lãi gộp năm 2013 | |
| aw050 | Số lượng bán năm 2013 | |
| aw051 | Số tài chính năm 2013 | |
| aw052 | Doanh thu theo nhân viên bán hàng năm 2013 | |
| aw053 | Doanh thu Internet theo lý do mua hàng năm 2013 | |
| aw054 | Dự báo doanh thu năm 2015 | |
| aw055 | Doanh thu năm 2009 | |
| aw056 | Doanh thu Internet tháng 3/2015 | |
| aw057 | Số cuộc gọi tổng đài tháng 1/2013 | |
| aw058 | Doanh số Trade Sales theo ngân sách năm 2013 | |
| aw059 | Giá cổ phiếu của công ty hôm nay | |
| aw060 | Tỷ lệ khách hàng rời bỏ năm 2013 | |
