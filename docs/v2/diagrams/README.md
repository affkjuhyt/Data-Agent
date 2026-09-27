# Sơ đồ V2

> Nguồn `.puml` và bản `.svg` render sẵn. Cập nhật 27/09/2026 khi chuyển các sơ đồ còn dùng được của
> thiết kế v1 sang V2 (đổi số, đổi ví dụ sang dầu/vàng, tô màu lại theo hiện trạng đo trên repo).
> Khi sơ đồ mâu thuẫn với [00](../00-architecture-v2.md) hoặc [03](../03-target-design.md), văn bản
> thắng; sửa sơ đồ.

## 1. Đọc sơ đồ

Cột **Tính chất** là thứ phải đọc đầu tiên: nhầm "đích" thành "hiện trạng" là cách hiểu sai nặng nhất.
HT = hiện trạng (đang chạy) · Đích = chưa tồn tại, là hợp đồng thiết kế · HH = vẽ đích, tô màu theo
mức đã có.

| Màu | Nền / viền | Nghĩa | Stereotype |
| --- | --- | --- | --- |
| Xanh lá | `#EEF7EE` / `#4E8A4E` | Đã có, chạy hôm nay | `<<reuse>>` |
| Xanh dương | `#E6F0FF` / `#2F6FB3` | Có một phần, cần mở rộng | `<<extend>>` |
| Vàng | `#FFF4D6` / `#C08A00` | Chưa có, đích V2 | `<<new>>` |

Sơ đồ có một trạng thái duy nhất ghi thẳng trong tiêu đề thay vì chú giải. Khi một năng lực chuyển
trạng thái, đổi màu ở mọi sơ đồ liên quan, nhất là [02](02-capability-status.puml) và
[18](18-use-cases.puml).

## 2. Danh mục

### Kiến trúc (00–18)

| # | Sơ đồ | Loại | Tính chất |
| --- | --- | --- | --- |
| [00](00-system-context.svg) ([puml](00-system-context.puml)) | Bối cảnh hệ thống (C4 mức 0): người dùng, nguồn dầu/vàng, GPU host | Bối cảnh | HH |
| [01](01-current-architecture.svg) ([puml](01-current-architecture.puml)) | Kiến trúc hiện tại đo trên manifest | Thành phần | HT |
| [02](02-capability-status.svg) ([puml](02-capability-status.puml)) | Trạng thái năng lực theo lớp V2 | Thành phần | HH |
| [03](03-layers.svg) ([puml](03-layers.puml)) | Các lớp của 00 §2 và nơi hiện thực (03 §1) | Thành phần | HH |
| [04](04-service-landscape.svg) ([puml](04-service-landscape.puml)) | Deployable hiện có + `indicator-api`, `forecast-worker`, consumer mới | Thành phần | HH |
| [05](05-service-connections.svg) ([puml](05-service-connections.puml)) | Kết nối REST · Kafka · MCP · SSE, topic hiện có và topic V2 | Thành phần | HH |
| [06](06-database-ownership.svg) ([puml](06-database-ownership.puml)) | Kho và schema theo chủ sở hữu (10 §4) | Thành phần | HH |
| [07](07-data-model.svg) ([puml](07-data-model.puml)) | Mô hình dữ liệu chuẩn: bốn trục thời gian, effective version, review, knowledge | ERD | HH (phần lớn đích) |
| [08](08-ingestion.svg) ([puml](08-ingestion.puml)) | Nguồn → raw bất biến → hai pipeline → DQ → `obs` | Thành phần | HH |
| [09](09-dataflow-proactive.svg) ([puml](09-dataflow-proactive.puml)) | Luồng chủ động của 00 §10 | Hoạt động | HH |
| [10](10-release-to-effective.svg) ([puml](10-release-to-effective.puml)) | Một release EIA mới đi tới effective observation | Hoạt động (làn) | HH |
| [11](11-state-machines.svg) ([puml](11-state-machines.puml)) | Vòng đời release, mapping, ReviewCase, alert, knowledge proposal | Máy trạng thái | Đích |
| [12](12-query-sequence.svg) ([puml](12-query-sequence.puml)) | Câu hỏi Brent của 00 §8 qua các service | Tuần tự | HH |
| [13](13-agent-core-mcp.svg) ([puml](13-agent-core-mcp.puml)) | Agent core: capability registry, task graph, verifier, một MCP Gateway | Thành phần | HH |
| [14](14-alert-investigation.svg) ([puml](14-alert-investigation.puml)) | Observation mới → điều tra → một alert (replay 23/09/2026) | Hoạt động | HH (gần hết đích) |
| [15](15-authz-audit.svg) ([puml](15-authz-audit.puml)) | Xác thực, phân quyền, ghi vết | Tuần tự | HH |
| [16](16-number-provenance.svg) ([puml](16-number-provenance.puml)) | Đường đi của một con số từ ô/trang nguồn tới câu trả lời | Hoạt động | HH |
| [17](17-technique-stack.svg) ([puml](17-technique-stack.puml)) | Bản đồ kỹ thuật theo tầng, cờ đang bật/tắt | Thành phần | HH |
| [18](18-use-cases.svg) ([puml](18-use-cases.puml)) | UC01–UC14 theo vai trò | Ca sử dụng | HH |

### Giao diện (20–26)

| # | Sơ đồ | Loại | Tính chất |
| --- | --- | --- | --- |
| [20](20-ui-ia.svg) ([puml](20-ui-ia.puml)) | Bản đồ điều hướng: module hiện có + màn V2 (03 §14) | Sơ đồ tư duy | HH |
| [21](21-data-studio.svg) ([puml](21-data-studio.puml)) | Chức năng Data Studio | Sơ đồ tư duy | HH |
| [22](22-render-contract.svg) ([puml](22-render-contract.puml)) | Capability → khối trả lời → frame SSE | Thành phần | HH |
| [23](23-frontend-arch.svg) ([puml](23-frontend-arch.puml)) | Kiến trúc `frontend/src` | Thành phần | HH |
| [24](24-ask.svg) ([puml](24-ask.puml)) | Bố cục Ask / Analyst Workspace | Thành phần | HH |
| [25](25-uf-analyst.svg) ([puml](25-uf-analyst.puml)) | Luồng chuyên viên phân tích (bước 24–27) | Hoạt động | HH |
| [26](26-uf-reviewer.svg) ([puml](26-uf-reviewer.puml)) | Luồng reviewer/checker xử lý ReviewCase (bước 10–15) | Hoạt động (làn) | HH |

### Vận hành (30–42), đọc cùng [11-ops.md](../11-ops.md)

| # | Sơ đồ | Loại | Tính chất |
| --- | --- | --- | --- |
| [30](30-cicd.svg) ([puml](30-cicd.puml)) | CI/CD hôm nay: GitHub Actions + Ansible | Hoạt động | HT |
| [31](31-gitops.svg) ([puml](31-gitops.puml)) | GitOps đích: build, ký image, Argo CD | Thành phần | HH |
| [32](32-envs.svg) ([puml](32-envs.puml)) | Môi trường dev và production, đường promote | Triển khai | HH |
| [33](33-infra.svg) ([puml](33-infra.puml)) | Hạ tầng một node k3s + GPU ngoài k8s, đích HA | Triển khai | HH |
| [34](34-network.svg) ([puml](34-network.puml)) | Mạng và giao thức, xác thực từng chặng | Thành phần | HH |
| [35](35-kafka.svg) ([puml](35-kafka.puml)) | Kafka: cấu hình, topic, producer/consumer | Thành phần | HH |
| [36](36-observability.svg) ([puml](36-observability.puml)) | Metrics · log · trace · LLM · dữ liệu | Thành phần | HH |
| [37](37-incident.svg) ([puml](37-incident.puml)) | Cảnh báo vận hành và xử lý sự cố | Hoạt động | HH |
| [38](38-dataops.svg) ([puml](38-dataops.puml)) | Sức khoẻ dữ liệu và pipeline | Thành phần | HH |
| [39](39-backup-dr.svg) ([puml](39-backup-dr.puml)) | Backup và DR theo kho (P0-07) | Thành phần | HH |
| [40](40-supply-chain.svg) ([puml](40-supply-chain.puml)) | Chuỗi cung ứng phần mềm: cổng từ commit tới cụm | Hoạt động | HH |
| [41](41-mlops-lifecycle.svg) ([puml](41-mlops-lifecycle.puml)) | Vòng đời thay đổi model, prompt, forecast, skill | Hoạt động | HH |
| [42](42-mlops-arch.svg) ([puml](42-mlops-arch.puml)) | Kiến trúc MLOps/LLMOps | Thành phần | HH |

## 3. Render và kiểm lỗi

```bash
make diagrams      # docker plantuml/plantuml:1.2024.8, render mọi *.puml trong thư mục này
```

**Phải ghim 1.2024.8.** Bản `latest` (1.2026.x) coi cú pháp tô màu action `#màu:...;` là lỗi thời: báo
lỗi trong nhánh `if/else` của sơ đồ hoạt động và vẽ sai ở chỗ khác; cú pháp mới `:...; <<#màu>>` thì
1.2024.8 lại chưa hỗ trợ. Sơ đồ hoạt động ở đây dùng cú pháp cũ. Nếu extension IDE dùng bản mới, xem
file `.svg`.

PlantUML vẽ lỗi thành ảnh chứ không luôn dừng, nên sau khi render phải soi:

```bash
grep -lE 'Syntax Error|Some diagram description contains errors|Cannot find|\[From [^]]*line' docs/v2/diagrams/*.svg
```

Lệnh không in gì là sạch. Không commit thư mục `?/` (bộ đệm font của Java); target `make diagrams`
đặt `user.home=/tmp` để nó không xuất hiện.

## 4. Bảo trì

| Khi nào | Làm gì |
| --- | --- |
| Thêm sơ đồ | Số tiếp theo trong nhóm; thêm dòng vào mục 2; chú giải ba màu |
| Một năng lực đổi trạng thái | Đổi màu ở mọi sơ đồ liên quan, trong cùng commit với mã |
| Trước buổi trình bày | `make diagrams`, chạy lệnh soi mục 3, đọc cột Tính chất để không trình bày đích thành hiện trạng |
