# INFRA\-DESIGN

# Hạ tầng, mạng, giao thức, hàng đợi và tối ưu — đánh giá và mục tiêu

> Phiên bản 16/09/2026 · Đối tượng: DevOps/SRE, kiến trúc sư hạ tầng, trưởng nhóm backend\.<br>Bổ sung cho [OPS\\\-DESIGN\\\.md](OPS-DESIGN.md) — tài liệu đó trả lời *cần có gì* \(GitOps, backup/DR, SLO, MLOps\); tài liệu này đi vào **kỹ thuật hạ tầng**: mạng, giao thức, hàng đợi thông điệp, tối ưu, năng lực, và **mục tiêu định lượng để đáp ứng thiết kế** đã định nghĩa\.<br>Căn cứ: đọc `deploy/k8s/production/\*\*`, `deploy/docker/kong`, `backend/libs/database\_connector`, `platform\_workers/common/kafka\.py` ngày 16/09/2026\.<br>Sơ đồ: [51](diagrams/51-network-protocol-topology.puml) mạng &amp; giao thức · [52](diagrams/52-kafka-topology.puml) Kafka · [28](diagrams/28-infra-topology.puml) hạ tầng · [29](diagrams/29-observability-architecture.puml) giám sát\.

## 0\. Tóm tắt

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750313&cot=14)*

| \# | Kết luận |
| --- | --- |
| 1 | Nền k8s làm chuẩn hơn mặt bằng chung: 3 namespace, default\-deny NetworkPolicy cả ingress lẫn egress, PodSecurity restricted, HPA trên 4 service, và runtimeClassName: gvisor cho sandbox\-executor — cách ly nhân cho mã do agent sinh, hiếm thấy ở mức dự án\. |
| 2 | Nhưng toàn bộ bảo đảm độ bền đang là danh nghĩa: 1 broker Kafka với replication\.factor = 1 cho mọi topic \(gồm \_\_consumer\_offsets\)\. Producer đặt acks=all mà min\.insync\.replicas = 1 nên acks=all không mang lại bảo đảm nào\. |
| 3 | Ba lỗ hổng thứ tự và trùng lặp trong hàng đợi, phải bịt trước khi bật luồng dữ liệu ANKT: producer không truyền key \(mất thứ tự theo series\_key\) · không bật enable\.idempotence \(retry có thể vừa trùng vừa đảo thứ tự\) · 7 worker Kafka không có liveness/readiness \(consumer treo không bao giờ được khởi động lại\)\. |
| 4 | Một thành phần đang chạy mà không ai dùng: pgbouncer được triển khai, nhưng POSTGRES\_HOST trong ConfigMap trỏ thẳng tới postgres\. Đang tốn tài nguyên mà không có tác dụng — và nếu nối vào mà không xử lý statement\_cache\_size của asyncpg thì sẽ hỏng ngay \(mục 4\.3\)\. |
| 5 | Biên mạng chưa hoàn chỉnh: không có Ingress, không có TLS trong cluster, Kong là ClusterIP, Keycloak lộ qua NodePort 30110\. Và chưa có kiểm soát egress ra Internet — điều kiện bắt buộc trước khi bật connector tự động tới 38 nguồn ngoài\. |
| 6 | Giao thức có hai điểm chạm trần đã nhìn thấy trước: Kong read\_timeout = 60s chặn mọi luồng SSE dài hơn một phút \(báo cáo/bản tin tính bằng phút sẽ đứt\), và rate\-limiting dùng policy=local nên hạn mức thực tế nhân lên theo số replica HPA, không phải 300/phút như ghi\. |
| 7 | Năng lực: tổng requests khai báo là 11,4 core · 20,1 Gi \(1 replica mỗi workload\) trên một node duy nhất; tổng PVC khai báo 116 Gi trong khi ổ đĩa đã dùng 81%\. Con số này không đủ cho mục tiêu ANKT \(mục 5\)\. |
| 8 | Tối ưu đã làm tốt ở tầng ứng dụng \(cache 3 lớp, dynamic batching, invalidation qua Redis pub/sub\) nhưng chưa làm ở tầng hạ tầng: HPA chỉ theo CPU — tín hiệu sai cho workload chờ LLM; không có PDB; chỉ 1 startupProbe trong toàn bộ cây\. |
| 9 | Ước lượng: ~9 tuần\-người DevOps cho toàn bộ, trong đó ~2 tuần cho nhóm chặn \(P0\) ở mục 7\. |

## 1\. Hiện trạng — kiểm kê có căn cứ

### 1\.1 Compute và cách ly

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750320&cot=14)*

| Hạng mục | Hiện trạng | Đánh giá |
| --- | --- | --- |
| Cluster | k3s 1 node, 3 namespace \(dataagent\-service, \-database, \-sandbox\) | ⚠️ Mọi thứ là một miền lỗi |
| PodSecurity | restricted | ✅ |
| Cách ly sandbox | runtimeClassName: gvisor trên sandbox\-executor \(production\) | ✅ Cách ly nhân, đúng cho mã do agent sinh\. ⚠️ Dev đã gỡ vì cluster không có gvisor — phải xác nhận cluster production thực sự có RuntimeClass này, nếu không nó im lặng không được áp dụng |
| HPA | kong 2–6 · workflow\-runtime 2–8 · kb\-backend 3–12 · model\-service 2–4 | ⚠️ Chỉ theo CPU |
| Requests/limits | Có ở hầu hết; sandbox\-runner không có | ⚠️ Pod không có request bị xếp lớp QoS thấp nhất, bị giết trước khi node ép bộ nhớ |
| Probe | 19/26 workload có liveness\+readiness; 7 worker Kafka không có gì; 1 startupProbe duy nhất \(kibana\) | ⚠️ Mục 3\.3 |
| PDB | Không có trên toàn bộ cây | ⚠️ Nâng cấp node sẽ đánh rơi cả 3 replica kb\-backend cùng lúc |
| Affinity / topology spread | Không có | Chấp nhận được với 1 node; chặn khi lên HA |
| GPU | Ngoài cluster, docker run, không device plugin, không ghim version | ⚠️ Không lập lịch được, không quota được |

### 1\.2 Mạng

Sơ đồ [51\\\-network\\\-protocol\\\-topology](diagrams/51-network-protocol-topology.puml)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750327&cot=14)*

| Hạng mục | Hiện trạng | Đánh giá |
| --- | --- | --- |
| NetworkPolicy | default\-deny cả Ingress lẫn Egress ở mỗi namespace, cộng allow\-dns \(UDP/TCP 53 tới kube\-system\) và allow\-egress\-to\-platform | ✅ Làm đúng: deny trước, mở sau |
| Namespace sandbox | Cách ly hoàn toàn — mọi gói ingress/egress bị chặn | ✅ |
| Ingress | Không có Ingress resource nào; Kong là ClusterIP; Keycloak NodePort 30110 | ⚠️ Không có điểm vào TLS thống nhất |
| TLS | Không có cert\-manager, không có TLS trong cluster | ⚠️ |
| mTLS Kong→upstream | Có script sinh cert cho compose \(deploy/docker/certs\), không có trong manifest k8s | ⚠️ |
| Egress ra Internet | Không có allowlist | ⚠️ Chặn trước khi bật connector: 38 nguồn ngoài nghĩa là 38 tên miền cần cho phép có chủ đích |
| Service mesh | Không có | ✅ Quyết định hợp lý ở quy mô 1 node — thay bằng service assertion ký HMAC ở tầng ứng dụng\. Cần ghi rõ đây là lựa chọn, không phải thiếu sót |
| GPU qua VPN | Mọi lời gọi embed/rerank/LLM đi qua ranh giới mạng | ⚠️ Thêm độ trễ và thêm một miền lỗi vào đường nóng của mọi câu hỏi |

### 1\.3 Giao thức

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750330&cot=14)*

| Kênh | Giao thức hiện tại | Ghi chú |
| --- | --- | --- |
| Trình duyệt → Kong | HTTP/1\.1 | Chưa có HTTP/2; với nhiều request song song của trang chỉ số \(4 request/lần mở\) HTTP/2 có lợi rõ |
| Kong → upstream | HTTP/1\.1, connect\_timeout 5s, read\_timeout 30s hoặc 60s tuỳ route | ⚠️ 60s là trần cứng cho SSE |
| Stream câu trả lời | SSE qua Kong | ⚠️ Chưa khai báo tường minh việc tắt buffering; đang chạy được nhưng phụ thuộc hành vi mặc định, không phải cấu hình có chủ đích |
| Nội bộ service→service | HTTP/1\.1 \+ X\-Service\-Assertion \(HMAC có hạn\) | ✅ Cơ chế đúng |
| Tracing | OTLP gRPC → Jaeger | ✅ |
| Kafka | SASL\_PLAINTEXT \(SASL/PLAIN, không TLS\) | ⚠️ Mật khẩu và payload đi trần trong cluster |
| Postgres | asyncpg \(TCP\) \+ set\_config\('app\.tenant\_id', …, true\) cho RLS | ✅ Tham số true = phạm vi transaction, đúng cho pooling transaction\-mode |
| ClickHouse | HTTP qua clickhouse\_connect, không tái dùng kết nối \(ghi chú trong chính mã\) | ⚠️ Mục 4\.3 |
| Neo4j / Qdrant / MinIO | Bolt / REST / S3 | ✅ |
| MCP | Chưa có \(node mcp gọi HTTP tự định dạng\) | Xem TECH\-DESIGN T9 — khi làm sẽ là kết nối dài qua Kong, phải tính cùng bài toán timeout ở mục 3\.2 |

### 1\.4 Hàng đợi thông điệp

Sơ đồ [52\\\-kafka\\\-topology](diagrams/52-kafka-topology.puml)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750336&cot=14)*

| Hạng mục | Hiện trạng | Đánh giá |
| --- | --- | --- |
| Broker | 1, KRaft \(broker\+controller cùng tiến trình\), apache/kafka:3\.7\.0, heap 512m, PVC 8Gi | ⚠️ |
| Replication factor | 1 cho mọi topic, gồm \_\_consumer\_offsets và transaction state log | ⚠️ Mất broker = mất offset của mọi consumer group |
| acks | acks="all", retries=5 | ⚠️ Vô nghĩa khi RF=1: min\.insync\.replicas chỉ có thể là 1 |
| Idempotent producer | Không bật | ⚠️ retries=5 không idempotence có thể sinh bản ghi trùng và đảo thứ tự trong cùng phân vùng |
| Khoá phân vùng | workflow\_runtime/kafka\_producer\.py có truyền key; platform\_workers/common/kafka\.py và release\_worker gửi không key | ⚠️ Round\-robin → mất thứ tự theo thực thể |
| auto\.create\.topics\.enable | true | ⚠️ Topic sai chính tả được tạo im lặng, không ai biết consumer đang nghe nhầm |
| ACL | StandardAuthorizer, allow\.everyone\.if\.no\.acl\.found=false, ACL theo principal từng service | ✅ Thiết kế đúng… |
| …nhưng | super\.users gồm User:ANONYMOUS, trong khi listener INTERNAL:8352 là PLAINTEXT | ⚠️ Ai tới được cổng 8352 là superuser\. NetworkPolicy đang che, nhưng đây là phòng thủ một lớp |
| Topic | 5 nhóm đang tạo \(audit\.data\.access 6 phân vùng/90 ngày · release\.pipeline\.\* 3 phân vùng không đặt retention · memory\.extraction\.\* · ddl\.changes · ingest 3 phân vùng\) | ⚠️ 17 topic của luồng ANKT chưa tạo; indicator\.version\.approved cần compacted |
| DLQ | Duy nhất một: memory\.extraction\.dlq | ⚠️ Thiết kế yêu cầu \*\.dlq cho mọi consumer |
| Consumer | enable\_auto\_commit=False, idempotent theo source\_event\_id | ✅ Hai kỹ thuật đúng |
| Giám sát lag | Không có | ⚠️ |

### 1\.5 Lưu trữ và tối ưu

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750345&cot=14)*

| Hạng mục | Hiện trạng | Đánh giá |
| --- | --- | --- |
| PVC khai báo | kafka 8 · es 8 · openbao 5 · clickhouse 10\+5 · minio 20 · redis 5 · postgres 20 · qdrant 10 · mongo 10 · neo4j 10\+5 = 116 Gi | ⚠️ Ổ đĩa node đã dùng 81% \(OPS\-DESIGN mục 1\.3\) |
| StorageClass | local\-path \(k3s mặc định\) | ⚠️ Không replica, không snapshot; mất node = mất dữ liệu |
| Cache ứng dụng | 3 lớp: Redis khớp chính xác \(L0\) → Qdrant ngữ nghĩa \(0\.92 · TTL 24h\) → cache embedding \+ dynamic batching; invalidation đa replica qua Redis pub/sub | ✅ Làm tốt và đúng thứ tự \(rẻ trước, đắt sau\) |
| Connection pooling | SQLAlchemy pool\_size \+ max\_overflow cho Postgres; ClickHouse không tái dùng kết nối; pgbouncer không nằm trong đường đi | ⚠️ |
| Rate limit | Kong rate\-limiting 300/phút, policy=local | ⚠️ Với HPA 2–6 replica, hạn mức thực tế là 600–1800/phút và thay đổi khi autoscale |
| Ghim image | Toàn bộ :latest, gồm openbao/openbao:latest và edoburu/pgbouncer:latest | ⚠️ Không tái lập được; rollback không xác định |

## 2\. Mục tiêu định lượng để đáp ứng thiết kế

Suy ra từ NFR đã nêu \([SERVICE\\\-DESIGN](SERVICE-DESIGN.md) mục 10, [OPS\\\-DESIGN](OPS-DESIGN.md) mục 4\.2\) và khối lượng dữ liệu dự kiến\. Đây là **mục tiêu hạ tầng**, khác với SLO sản phẩm\.

### 2\.1 Độ trễ và thông lượng

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750351&cot=14)*

| Chỉ tiêu | Mục tiêu | Ngân sách hạ tầng suy ra |
| --- | --- | --- |
| Trang chỉ số p95 | &lt; 1,5 s | 4 request song song qua Kong; ngân sách mỗi request ≈ 1,2 s\. Kong overhead ≤ 20 ms · catalog \(cache 5 phút\) ≤ 50 ms · compute ≤ 400 ms · kb ≤ 300 ms · alert ≤ 100 ms\. Bắt buộc HTTP/2 hoặc keep\-alive, và cache catalog ở Kong |
| Truy vấn 1 chuỗi 50 năm | &lt; 100 ms p95 | ClickHouse: phân vùng theo năm, ORDER BY \(tenant, series\_key, period\_start, release\_date\); kết quả ≈ 50–200 dòng\. Đạt được nếu ClickHouse có ≥ 2 core và ≥ 4 Gi page cache cho phần nóng |
| TTFT câu tra cứu | ≤ 2 s | Chuỗi: Kong → runtime → MCP → compute → LLM đầu ra token đầu\. LLM qua VPN chiếm phần lớn; ngân sách mạng VPN ≤ 30 ms RTT, nếu hơn thì phải đưa GPU vào cùng mạng |
| Câu đa chỉ số | hoàn tất ≤ 30 s | Kong read\_timeout phải ≥ 120 s cho route stream \(mục 3\.2\) |
| Báo cáo/bản tin | phút, chạy nền | Không đi qua SSE đồng bộ: job nền \+ thông báo khi xong |
| Ghi bulk observation | ≥ 100k dòng/phút | ClickHouse insert theo lô ≥ 10k dòng/lần, async\_insert bật cho đường sự kiện nhỏ lẻ |
| Đồng thời người dùng | 50 CCU giai đoạn đầu | workflow\-runtime 2–8 replica × ~10 SSE đồng thời/replica; SSE là kết nối dài nên giới hạn là số kết nối, không phải CPU → mục 4\.1 |

### 2\.2 Độ bền và khả dụng

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750363&cot=14)*

| Chỉ tiêu | Mục tiêu | Yêu cầu hạ tầng |
| --- | --- | --- |
| Khả dụng API | ≥ 99,5%/30 ngày \(≈ 3,6 h ngừng/tháng\) | Không đạt được với 1 node: một lần reboot sửa driver GPU đã tiêu gần hết ngân sách\. Cần ≥ 3 node |
| Không mất sự kiện | 0 sự kiện mất | Kafka RF=3, min\.insync\.replicas=2, enable\.idempotence=true |
| Thứ tự theo thực thể | Đảm bảo trong một series\_key | Producer phải truyền key; consumer group không rebalance giữa chừng \(tăng max\.poll\.interval\.ms cho worker chậm\) |
| RPO / RTO | RPO ≤ 24h, RTO ≤ 4h \(đề xuất, chốt cùng nghiệp vụ\) | Backup ngoài site; hiện backup Postgres nằm cùng node với dữ liệu ⚠️ |
| Không mất quan sát đã duyệt | Tuyệt đối | Observation append\-only \+ MinIO versioning bucket ankt\-raw |

### 2\.3 Năng lực

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750379&cot=14)*

| Tài nguyên | Mục tiêu \(100 chỉ số, 5 năm\) | Hiện có |
| --- | --- | --- |
| CPU | ~20 core ở tải mục tiêu \(11,4 core requests × HPA minima \+ ingestion\) | 1 node dùng chung |
| RAM | ~40 Gi | ~20 Gi requests hiện tại |
| Đĩa dữ liệu | ClickHouse &lt; 5 Gi nén \(10–50 triệu quan sát\) · MinIO 50–200 Gi \(PDF \+ ảnh trang dẫn xuất gấp 3–5 lần\) · Postgres &lt; 10 Gi · Qdrant vài chục Gi | PVC khai báo 116 Gi, ổ đã dùng 81% ⚠️ |
| GPU | Chat thường trực \(~32 Gi/40 Gi đã cấp\) \+ trích xuất theo lô | Còn ~8 Gi, không đủ cho VLM 7B thường trực |

> **Kết luận năng lực**: MinIO là hạng mục vượt trần trước tiên\. 50–200 Gi cho tài liệu ANKT không nằm vừa trên ổ hiện tại\. Đây là **quyết định mua sắm, không phải quyết định kỹ thuật** — cần nêu trong báo cáo\.

## 3\. Kỹ thuật cần bổ sung — mạng và giao thức

### 3\.1 Biên: Ingress, TLS, egress

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750407&cot=14)*

| Việc | Kỹ thuật đề xuất | Vì sao |
| --- | --- | --- |
| Điểm vào thống nhất | Ingress \(Traefik sẵn trong k3s, hoặc ingress\-nginx\) \+ cert\-manager; chỉ Kong được public; gỡ NodePort của Keycloak | Hiện không có nơi cắt TLS, và NodePort là bề mặt tấn công thừa |
| TLS nội bộ | Bước 1: TLS ở biên\. Bước 2: mTLS Kong→upstream \(script sinh cert đã có cho compose, đưa vào manifest\) | Không cần service mesh để đạt mức này |
| Kiểm soát egress | NetworkPolicy egress theo allowlist tên miền cho pod connector; đi qua một egress proxy \(ví dụ Squid hoặc Envoy\) vì NetworkPolicy chỉ hiểu CIDR, không hiểu tên miền | Bắt buộc trước khi bật connector: một agent có thể bị dụ gọi ra bất kỳ đâu; đây cũng là nơi ghi log mọi lượt tải về |
| Đưa GPU vào cùng mạng | NVIDIA GPU Operator \+ vLLM/TEI thành Deployment, hoặc tối thiểu đặt GPU host cùng L2/VPN có RTT ≤ 30 ms, đo và giám sát | Mọi câu hỏi đều đi qua chặng này |

### 3\.2 Giao thức: SSE, timeout, HTTP/2

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750502&cot=14)*

| Việc | Kỹ thuật đề xuất |
| --- | --- |
| Trần timeout SSE | Tách route riêng cho /api/orchestrate/stream và /notifications/stream với read\_timeout ≥ 120000; giữ 30–60 s cho route thường để không che lỗi treo\. Báo cáo dài không dùng SSE đồng bộ mà chuyển job nền \+ thông báo |
| Tắt buffering tường minh | Đặt KONG\_NGINX\_PROXY\_PROXY\_BUFFERING: "off" cho route stream, và ứng dụng trả X\-Accel\-Buffering: no \+ Cache\-Control: no\-cache\. Hiện đang dựa vào hành vi mặc định — một lần nâng cấp Kong có thể làm đứt stream mà không ai hiểu vì sao |
| Heartbeat | Gửi comment SSE \(: ping\) mỗi 15 s để proxy trung gian và load balancer không đóng kết nối rỗi |
| HTTP/2 ở biên | Bật trên Ingress → giảm chi phí 4 request song song của trang chỉ số |
| Nội bộ | Giữ HTTP/1\.1 \+ keep\-alive; chưa cần gRPC\. Nếu sau này cần, ưu tiên đường compute → observation \(gọi nhiều, payload nhỏ\) |
| Kafka TLS | Chuyển SASL\_PLAINTEXT → SASL\_SSL, tối thiểu cho listener ngoài node |

### 3\.3 Độ tin cậy chạy được

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750507&cot=14)*

| Việc | Chi tiết |
| --- | --- |
| Probe cho 7 worker Kafka | kb\-promote\-silver/gold, kb\-structured\-data, kb\-conflict\-resolution, platform\-workers\-\* \(audit/memory/schema\-crawler\), sandbox\-runner\. Liveness không nên chỉ là "tiến trình còn sống" mà là "consumer còn poll trong N giây qua" — một consumer treo ở tầng ứng dụng vẫn còn tiến trình |
| startupProbe | Cho các dịch vụ khởi động chậm \(Keycloak, Neo4j, Langfuse, workflow\-runtime có migrate init\) — nếu không, liveness giết pod trong lúc nó đang khởi động bình thường |
| PDB | minAvailable: 1 \(hoặc 50%\) cho mọi Deployment ≥ 2 replica |
| sandbox\-runner | Bổ sung requests/limits — pod không có request nằm ở QoS BestEffort, bị giết đầu tiên khi node ép bộ nhớ |
| Xác nhận gvisor | Kiểm tra RuntimeClass gvisor tồn tại trên cluster production\. Nếu không, runtimeClassName khiến pod không lên lịch được — hoặc tệ hơn, ai đó gỡ nó ra và mất cách ly mà không ghi nhận |

## 4\. Kỹ thuật cần bổ sung — hàng đợi và dữ liệu

### 4\.1 Kafka: từ "chạy được" sang "không mất dữ liệu"

Thứ tự ưu tiên, rẻ trước:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750513&cot=14)*

| \# | Việc | Chi tiết kỹ thuật |
| --- | --- | --- |
| K1 | Bật enable\.idempotence=true ở mọi producer | Một dòng cấu hình\. Cùng với acks=all cho exactly\-once trong một phân vùng, và chặn đảo thứ tự khi retry\. Không cần transaction |
| K2 | Truyền key ở mọi producer | platform\_workers/common/kafka\.py cần key\_serializer và mọi send\(\) phải có key theo SERVICE\-DESIGN mục 4\.3: source\_id · release\_id · series\_key · indicator\_id · conversation\_id |
| K3 | auto\.create\.topics\.enable=false \+ tạo topic khai báo trong job | Topic sai chính tả phải lỗi to, không im lặng |
| K4 | Gỡ User:ANONYMOUS khỏi super\.users; cấp principal riêng cho listener INTERNAL | Phòng thủ nhiều lớp, không dựa một mình vào NetworkPolicy |
| K5 | RF=3 \+ min\.insync\.replicas=2 | Cần ≥ 3 broker → đi cùng việc lên 3 node \(mục 6\)\. Đến lúc đó, acks=all mới có nghĩa |
| K6 | DLQ cho mọi consumer \+ retry có backoff | Mẫu đã có ở memory\.extraction\.dlq; nhân rộng thành quy ước: &lt;topic&gt;\.dlq, retention 30 ngày, có alert khi có bản ghi mới |
| K7 | Retention và cleanup khai báo tường minh cho mọi topic | indicator\.version\.approved → cleanup\.policy=compact, giữ vĩnh viễn\. observation\.published → 90 ngày\. release\.pipeline\.\* hiện không đặt gì nên đang dùng mặc định 7 ngày |
| K8 | Giám sát consumer lag | kafka\-exporter → Prometheus; alert KafkaConsumerLagHigh đã thiết kế ở OPS\-DESIGN mục 4\.3 nhưng chưa có nguồn số liệu |
| K9 | Phân vùng cho luồng ANKT | observation\.published là topic nóng nhất: đề xuất 6 phân vùng \(đủ song song, vẫn giữ thứ tự theo series\_key\)\. Các topic quy trình giữ 3 |

> **Lưu ý về thứ tự**: K1\+K2 cùng nhau mới đủ\. Idempotence chặn trùng/đảo *trong một phân vùng*; key quyết định *phân vùng nào*\. Thiếu key thì hai sự kiện của cùng một chuỗi vẫn rơi hai phân vùng và được xử lý song song — cache giá trị chỉ số có thể bị ghi đè bằng bản cũ hơn\.

### 4\.2 ClickHouse cho observation

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750518&cot=14)*

| Việc | Chi tiết |
| --- | --- |
| Bảng | ReplacingMergeTree\(ingested\_at\), PARTITION BY toYear\(period\_start\), ORDER BY \(tenant\_id, series\_key, period\_start, release\_date, release\_id\) \(SERVICE\-DESIGN mục 3\.2\) |
| Bẫy gộp bất đồng bộ | ReplacingMergeTree gộp khi nào nó muốn\. Mọi truy vấn phải dùng argMax\(value, release\_date\) hoặc FINAL; không được giả định đã gộp\. Cần test với hai bản ghi cùng khoá chưa gộp |
| Ghi | Lô ≥ 10k dòng; bật async\_insert \+ wait\_for\_async\_insert=1 cho đường sự kiện nhỏ lẻ, tránh sinh hàng nghìn part nhỏ |
| Tách cụm | ClickHouse hiện đang phục vụ Langfuse\. Tách database riêng \(obs\), và cân nhắc tách instance khi khối lượng tăng — trace LLM và quan sát kinh tế có hình dạng tải khác hẳn nhau |
| Row policy | Theo tenant\_id và classification, đồng bộ với quyết định của auth\-api |

### 4\.3 Postgres, pooling, kết nối

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750521&cot=14)*

| Việc | Chi tiết |
| --- | --- |
| Quyết định về pgbouncer | Hai lựa chọn, phải chọn một: \(a\) gỡ bỏ — đang tốn tài nguyên mà không ai dùng; \(b\) nối vào — đổi POSTGRES\_HOST sang pgbouncer\. Nếu chọn \(b\), bắt buộc đặt statement\_cache\_size=0 cho asyncpg \(tham số đã có sẵn trong postgres\_connector\.py\), vì transaction pooling không giữ prepared statement giữa các transaction\. Đây là lỗi kinh điển và biểu hiện của nó là prepared statement "\_\_asyncpg\_stmt\_x\_\_" does not exist xuất hiện ngẫu nhiên dưới tải |
| LangGraph checkpointer | Dùng psycopg3 \(driver khác asyncpg\) với pool riêng\. Nếu nối pgbouncer phải kiểm tra riêng đường này — hai driver, hai hành vi |
| DEFAULT\_POOL\_SIZE: 20 | Với workflow\-runtime HPA tới 8 replica \+ kb\-backend tới 12, 20 kết nối server\-side là điểm nghẽn tiềm tàng\. Tính theo tải thực, đừng để mặc định |
| Tái dùng kết nối ClickHouse | Mã hiện tạo client mỗi lần gọi, có ghi chú lý do \(tránh dùng lẫn session giữa tenant\)\. Với truy vấn chuỗi mục tiêu &lt; 100 ms, chi phí bắt tay HTTP mỗi lần là đáng kể → dùng pool theo tenant thay vì per\-call |

### 4\.4 Lưu trữ

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750537&cot=14)*

| Việc | Chi tiết |
| --- | --- |
| Rời local\-path | Longhorn \(replica 2–3\) khi lên nhiều node; trước đó tối thiểu đưa backup ra khỏi node — hiện backup Postgres nằm cùng ổ với dữ liệu, nên mất ổ là mất cả hai |
| MinIO | Bucket ankt\-raw bật versioning; đặt lifecycle cho ankt\-derived \(ảnh trang có thể sinh lại được → giữ ngắn hơn\); tính trước 50–200 Gi |
| Cảnh báo dung lượng | disk\-alert\.sh hiện chỉ ghi log\. Nối vào Prometheus \+ predict\_linear \(alert DiskFillingUp đã thiết kế\) |

## 5\. Tối ưu

### 5\.1 Tự mở rộng đúng tín hiệu

**Vấn đề**: HPA chỉ theo CPU\. `workflow\-runtime` phần lớn thời gian **chờ LLM** — CPU thấp trong khi hàng đợi dài\. HPA sẽ không mở rộng đúng lúc, và có thể thu hẹp khi đang nghẽn\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750541&cot=14)*

| Việc | Kỹ thuật |
| --- | --- |
| Chỉ số tuỳ chỉnh cho HPA | prometheus\-adapter: mở rộng workflow\-runtime theo số SSE đang mở / replica hoặc độ sâu hàng đợi, không theo CPU |
| sandbox\-executor | Mở rộng theo độ dài hàng đợi job theo kind \(job\_queue\.py đã có số liệu, chỉ cần phơi ra Prometheus\) |
| KEDA cho worker Kafka | Mở rộng theo consumer lag — đúng bản chất workload; đây cũng chính là nguồn số liệu cho K8 |
| Ngưỡng thu hẹp | Đặt behavior\.scaleDown\.stabilizationWindowSeconds đủ dài \(≥ 300 s\) để không giết replica đang giữ SSE dài |

### 5\.2 Rate limit và bảo vệ quá tải

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750545&cot=14)*

| Việc | Kỹ thuật |
| --- | --- |
| Đổi policy=local → policy=redis | Redis đã có sẵn\. Hiện hạn mức thực tế nhân theo số replica và thay đổi mỗi lần autoscale — tức là không có hạn mức nào thật sự |
| Hạn mức theo người dùng, không theo IP | Dùng claim sub từ JWT; một phòng ban sau NAT không nên bóp lẫn nhau |
| Hạn mức riêng cho đường đắt | /api/orchestrate/stream và /api/sandbox/execute/\* cần hạn mức chặt hơn /api/indicators |
| Circuit breaker | Đã nêu trong SERVICE\-DESIGN mục 4\.1 \(timeout ≤ 5 s nội bộ, retry chỉ với GET\) — cần hiện thực bằng tenacity \(đã có trong requirements\) hoặc plugin Kong |

### 5\.3 Cache

Tầng ứng dụng đã tốt\. Bổ sung ở tầng hạ tầng:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750549&cot=14)*

| Việc | Kỹ thuật |
| --- | --- |
| Cache catalog ở Kong | GET /indicators/\{id\} đổi hiếm, TTL 5 phút — plugin proxy\-cache, giảm hẳn 1 trong 4 request của trang chỉ số |
| Tài sản tĩnh frontend | Ghim hash tên file \+ Cache\-Control: immutable; phục vụ qua Ingress, không qua Kong |
| Cache giá trị chỉ số | Khoá \(indicator\_version, vintage, engine\_version\); invalidate theo series\_key khi có observation\.published — tái dùng đúng mẫu Redis pub/sub đã có trong cache\_invalidation\.py |
| Không cache cái không nên | Câu trả lời có số liệu restricted không được vào cache dùng chung |

### 5\.4 GPU

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750556&cot=14)*

| Việc | Kỹ thuật |
| --- | --- |
| Đưa vào k8s | NVIDIA GPU Operator \+ device plugin; vLLM/TEI thành Deployment ghim version \(hiện docker run với tag trôi\) |
| Chia sẻ | MIG trên A100 \(cắt cứng, dễ dự đoán\) hoặc time\-slicing \(đơn giản hơn, không cách ly bộ nhớ\)\. Với chat cần độ trễ ổn định, MIG đúng hơn |
| Hàng đợi trích xuất | Ưu tiên thấp, chạy theo lô ngoài giờ cao điểm — hàng đợi riêng như job\_queue\.py đang làm cho sandbox |
| Trước khi đụng vào | Sửa lỗi driver NVML \(nvidia\-smi báo Driver/library version mismatch\) — cần reboot có kế hoạch \(README mục 2\) |

## 6\. Từ 1 node lên HA — thứ tự bắt buộc

Không thể nhảy thẳng\. Thứ tự dưới đây để mỗi bước đều chạy được:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750560&cot=14)*

| Bước | Việc | Điều kiện mở khoá |
| --- | --- | --- |
| 1 | Thêm 2 node \(control plane HA, etcd nhúng\) | Có phần cứng |
| 2 | topologySpreadConstraints \+ PDB cho mọi Deployment ≥ 2 replica | Bước 1 |
| 3 | Taint/affinity tách service / database / sandbox lên node khác nhau | Bước 1 |
| 4 | Longhorn thay local\-path, replica 2–3 | Bước 1 |
| 5 | Kafka 3 broker, RF=3, min\.insync\.replicas=2 | Bước 1 \+ 4 |
| 6 | Postgres replica \+ failover \(Patroni hoặc CloudNativePG\) | Bước 4 |
| 7 | Backup ra ngoài site | Độc lập — làm được ngay, không chờ |

> Bước 7 không phụ thuộc bước nào và đang là rủi ro cao nhất: backup nằm cùng ổ với dữ liệu\.

## 7\. Lộ trình

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750565&cot=14)*

| Ưu tiên | Việc | Tuần\-người | Vì sao xếp ở đây |
| --- | --- | --- | --- |
| P0 — chặn | K1 idempotence · K2 producer key · K3 tắt auto\-create · K4 gỡ ANONYMOUS superuser · probe cho 7 worker · requests cho sandbox\-runner · xác nhận gvisor · backup ra ngoài site | ~2 | Toàn bộ là cấu hình và vài chục dòng mã\. Không làm thì luồng dữ liệu ANKT sẽ mất thứ tự và trùng lặp ngay từ ngày đầu |
| P1 — trước khi bật connector | Ingress \+ cert\-manager TLS · egress allowlist qua proxy · timeout SSE theo route \+ tắt buffering tường minh \+ heartbeat · rate\-limit policy=redis · quyết định pgbouncer · PDB | ~2 | Điều kiện an toàn để mở kết nối ra 38 nguồn ngoài |
| P2 — trước Demo 2 | ClickHouse obs \+ async\_insert \+ pool theo tenant · K6 DLQ toàn hệ · K7 retention/compaction · K8 kafka\-exporter \+ lag alert · HPA theo chỉ số tuỳ chỉnh / KEDA · cache catalog ở Kong | ~2,5 | Đi cùng việc dựng observation store và ingestion plane |
| P3 — HA | 3 node · topology spread · Longhorn · Kafka 3 broker RF=3 · Postgres failover | ~2 | Cần phần cứng; là điều kiện để SLO 99,5% có nghĩa |
| P4 — GPU và tối ưu sâu | GPU Operator \+ MIG \+ ghim version · HTTP/2 ở biên · mTLS nội bộ · Kafka SASL\_SSL | ~1,5 | Sau khi ba mục trên ổn định |

**Tổng ~10 tuần\-người DevOps\.**

**Làm được ngay tuần này, chi phí gần bằng không:**

1. `enable\.idempotence=true` \+ `key\_serializer` trong `platform\_workers/common/kafka\.py` \(K1, K2\)\.
2. `KAFKA\_AUTO\_CREATE\_TOPICS\_ENABLE: 'false'` và gỡ `User:ANONYMOUS` khỏi `KAFKA\_SUPER\_USERS` \(K3, K4\)\.
3. Thêm liveness/readiness cho 7 worker; liveness kiểm **"còn poll trong N giây qua"**, không chỉ kiểm tiến trình\.
4. Thêm `resources` cho `sandbox\-runner`\.
5. Đổi `rate\-limiting` sang `policy=redis`\.
6. Chuyển backup Postgres ra khỏi node\.
7. Quyết định pgbouncer: gỡ, hoặc nối vào kèm `statement\_cache\_size=0`\.

## 8\. Quyết định hạ tầng cần chốt

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922750574&cot=14)*

| \# | Quyết định | Phương án | Đề xuất |
| --- | --- | --- | --- |
| ADR\-I1 | Số node | giữ 1 · 3 node HA | 3 node — SLO 99,5% không đạt được với 1 node |
| ADR\-I2 | Service mesh | không · Linkerd/Istio | Không — giữ service assertion HMAC; ghi rõ là quyết định, không phải thiếu sót |
| ADR\-I3 | pgbouncer | gỡ · nối vào | Nối vào kèm statement\_cache\_size=0, hoặc gỡ hẳn\. Không để nguyên trạng |
| ADR\-I4 | Kiểm soát egress | NetworkPolicy CIDR · egress proxy theo tên miền | Egress proxy — nguồn dữ liệu là tên miền, không phải IP cố định |
| ADR\-I5 | Chia sẻ GPU | MIG · time\-slicing | MIG — chat cần độ trễ ổn định |
| ADR\-I6 | Lưu trữ | local\-path · Longhorn · NFS | Longhorn khi lên nhiều node |
| ADR\-I7 | Tự mở rộng worker | HPA CPU · KEDA theo lag | KEDA theo consumer lag |
| ADR\-I8 | ClickHouse | dùng chung với Langfuse · tách instance | Tách database ngay, tách instance khi khối lượng tăng |
| ADR\-I9 | Giao thức nội bộ | HTTP/1\.1 · HTTP/2 · gRPC | Giữ HTTP/1\.1 \+ keep\-alive; HTTP/2 chỉ ở biên |
