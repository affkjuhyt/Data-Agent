# Gán nhãn bộ 30 tài liệu bake-off (PoC-1, DI-01)

Ground truth cho `make docling-bakeoff` (`docs/blueprint/DOCUMENT_INTELLIGENCE_SPEC.md` §11) gồm bốn thứ:

- ô bảng theo schema FinTabNet (row, col, span, text), kèm HTML cho TEDS;
- thứ tự đọc;
- chữ OCR (NFC);
- cây tiêu đề Chương/Mục/Điều/Khoản.

Label Studio (Apache-2.0) chạy trên máy, chỉ mở ở `127.0.0.1`, nên tài liệu không đi ra ngoài.

## Tài liệu để ở đâu

Tài liệu gốc **không vào repo**, vì giấy phép mỗi tài liệu khác nhau và bản scan có thể có dữ liệu cá nhân. Chúng nằm
trong `BAKEOFF_DOCS` (mặc định `~/.cache/dataagent/docs-bakeoff`), mỗi tài liệu một thư mục:

```text
~/.cache/dataagent/docs-bakeoff/
  vb-nd52-2015/                 doc_id: chữ, số, - và _
    original.pdf                hoặc original.png/.jpg/.tif; DOCX: giữ original.docx và xuất thêm original.pdf
    pages/page-001.png …        do make bakeoff-render sinh
    tables/<người gán>/t1.csv … sheet bảng của từng người
  exports/                      file JSON xuất từ Label Studio
  tasks.json                    do make bakeoff-render sinh
```

Repo giữ hai thứ: `tests/data/docs-bakeoff/sources.json` (sha256, kích thước, số trang; cột `kind`, `source`,
`licence` do người điền) và `tests/data/docs-bakeoff/gold/<doc_id>/` (ground truth đã đối chiếu).

## Chuẩn bị (một lần)

```bash
make bakeoff-render      # ảnh trang 150 dpi, tasks.json, cập nhật sources.json
make labelstudio         # http://127.0.0.1:8080; dừng: make labelstudio-stop
```

Trong Label Studio:

1. Tạo tài khoản cho từng người gán. Tài khoản chỉ nằm trên máy này, trong `~/.cache/dataagent/labelstudio`.
2. Tạo project "Bake-off", rồi vào *Settings → Labeling Interface → Code* và dán nội dung `page.xml`.
3. Vào *Settings → Cloud Storage → Add Source Storage → Local files*, đặt absolute path là `/label-studio/files/bakeoff`, lưu
   lại và **không** bấm Sync. Label Studio chỉ phục vụ file cục bộ khi có storage này.
4. Vào *Settings → Annotation*, bật cho phép nhiều annotation trên một task (hai người gán độc lập).
5. *Import* file `tasks.json`.

## Gán một trang

Với mỗi khối trên trang:

1. **Vẽ khung** sát khối, không xoay khung.
2. **Chọn nhãn:**
   - `Tiêu đề` là tên văn bản.
   - `Chương`, `Mục`, `Điều`, `Khoản` dùng cho văn bản pháp quy. Mỗi Điều (cả số và tên điều) là một khối; phần thân
     điều là `Đoạn văn`. `Điểm a, b, c` là `Mục liệt kê`.
   - `Heading 1/2/3` dùng cho tài liệu khác, ví dụ báo cáo tài chính.
   - `Đoạn văn`, `Mục liệt kê`, `Bảng`, `Hình`, `Chú thích hình/bảng`, `Chú thích cuối trang`, `Đầu trang`,
     `Chân trang` như tên gọi.
3. **Gõ chữ** đúng như trên trang: có dấu, giữ chính tả gốc kể cả lỗi, xuống dòng ở chỗ trang xuống đoạn. Không gõ chữ
   cho `Hình`.
4. **Ghi thứ tự đọc** bằng số, bắt đầu từ 1 trên mỗi trang và không trùng. Trang hai cột đọc hết cột trái rồi mới sang
   cột phải. Đầu trang và chân trang cũng có số.

**Bảng:** chữ của khối `Bảng` là tên sheet và số dòng tiêu đề, ví dụ `t1; header=1`. Bảng được chép vào
`tables/<người gán>/t1.csv`. `<người gán>` là đúng chuỗi dùng cho `ANNOTATOR` (id hoặc email Label Studio). Có thể gõ
trong Excel, LibreOffice hay Google Sheets rồi lưu CSV UTF-8.

- Mỗi ô trên trang là một ô trong sheet, theo đúng vị trí nhìn thấy.
- Ô gộp: ghi chữ ở ô trên cùng bên trái. Các ô bị nó phủ ghi `<` nếu cùng dòng, `^` nếu cùng cột, và `+` cho phần còn
  lại.
- Ô trống trên trang để trống trong sheet. Nếu một ô thật sự chứa ký tự `<`, `^` hoặc `+` thì gõ `\<`, `\^`, `\+`.
- Số giữ nguyên như in (`1.234,5`, `(12)`), không tự đổi định dạng.

Ví dụ: ô "Năm 2024" gộp hai cột, "Chỉ tiêu" gộp hai dòng.

```csv
Chỉ tiêu,Năm 2024,<
^,Q1,Q2
Doanh thu,"1.234,5",987
```

Khối bảng của ví dụ này ghi `t1; header=2`.

## Ra ground truth và đối chiếu hai người

1. Mỗi người gán xong thì xuất JSON: *Export → JSON* và lưu vào `~/.cache/dataagent/docs-bakeoff/exports/`.
2. Chạy:

```bash
make bakeoff-gold EXPORT=exports/<file>.json ANNOTATOR=<id hoặc email của A>   # → docs-bakeoff/gold-<A>/
make bakeoff-gold EXPORT=exports/<file>.json ANNOTATOR=<id hoặc email của B>
make bakeoff-agree A=<A> B=<B>
```

`bakeoff-gold` kiểm tra rồi mới ghi. Khi còn lỗi, nó **không ghi gì** và liệt kê từng lỗi kèm task, khung và ô:
- khung thiếu nhãn, thiếu thứ tự hoặc thiếu chữ;
- thứ tự đọc trùng hoặc nhảy số;
- sheet không có, sheet thừa;
- ô gộp sai dấu.

`bakeoff-agree` ghép khung của hai người theo IoU ≥ 0,5 rồi liệt kê mọi chỗ khác nhau: khung chỉ một người có, nhãn,
chữ (kèm số ký tự sai), thứ tự đọc, kích thước bảng, ô gộp và chữ từng ô. Hai người cùng xem lại trang gốc và sửa
trong Label Studio (và sheet) cho tới khi `bakeoff-agree` không còn khác biệt. Không ai được sửa theo bản của người kia
mà không nhìn trang. Sau đó ghi bản cuối vào repo:

```bash
make bakeoff-gold EXPORT=exports/<file>.json ANNOTATOR=<A> GOLD_OUT=$PWD/tests/data/docs-bakeoff/gold
```

Mỗi tài liệu ra bốn thứ:

| File | Nội dung |
| --- | --- |
| `layout.json` | từng trang, các khối theo thứ tự đọc: nhãn, loại, cấp tiêu đề, bbox (pixel của ảnh 150 dpi), chữ |
| `tables.json` | từng bảng: trang, bbox, `header_rows`, `cells` (row, col, rowspan, colspan, text, header), `html` |
| `headings.json` | cây tiêu đề: cấp (0 tên văn bản; Chương 1, Mục 2, Điều 3, Khoản 4; Heading n là n) |
| `ocr/page-NNN.txt` | chữ của trang theo thứ tự đọc, NFC; bảng đọc theo từng dòng |
