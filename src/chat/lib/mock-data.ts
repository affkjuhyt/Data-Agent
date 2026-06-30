import { Conversation, DataSource, ActiveAgent, Artifact, MCPTool, AgentSkill, SchemaTable, Project, ProjectFile } from "./types"

export const MCP_TOOLS: MCPTool[] = [
  { id: "hose_foreign_flow", name: "HOSE Foreign Flow",   description: "Dữ liệu ngoại mua/bán ròng realtime từ HOSE",   domain: ["stockllm"],              category: "data",     icon: "📈", isDefault: true  },
  { id: "yfinance",          name: "YFinance",            description: "Giá cổ phiếu, OHLCV, thông tin công ty",        domain: ["stockllm", "forecast"],  category: "data",     icon: "💹", isDefault: true  },
  { id: "neo4j_query",       name: "Neo4j Graph",         description: "Truy vấn knowledge graph quan hệ công ty/ngành", domain: ["stockllm", "enterprise"],category: "data",     icon: "🕸", isDefault: true  },
  { id: "macro_data",        name: "Macro Data",          description: "GDP, lạm phát, lãi suất, tỷ giá USD/VNĐ",       domain: ["stockllm", "forecast"],  category: "data",     icon: "🌐", isDefault: false },
  { id: "technical_calc",    name: "Technical Analysis",  description: "Tính RSI, MACD, Bollinger Bands từ OHLCV",       domain: ["stockllm"],              category: "analysis", icon: "📊", isDefault: false },

  { id: "snowflake_mcp",     name: "Snowflake DW",        description: "Query data warehouse doanh nghiệp",              domain: ["enterprise", "forecast"],category: "data",     icon: "❄️", isDefault: true  },
  { id: "dbt_query",         name: "dbt Semantic Layer",  description: "Query metric đã chuẩn hoá qua dbt",              domain: ["enterprise"],            category: "data",     icon: "🔧", isDefault: true  },
  { id: "anomaly_detect",    name: "Anomaly Detection",   description: "Phát hiện bất thường bằng Isolation Forest",     domain: ["enterprise", "forecast"],category: "analysis", icon: "🚨", isDefault: false },
  { id: "bi_render",         name: "BI Renderer",         description: "Tạo chart Plotly, export PDF báo cáo",           domain: ["enterprise", "stockllm"],category: "output",   icon: "📋", isDefault: false },

  { id: "vneid_mcp",         name: "VNeID",               description: "Xác thực CCCD/CMND qua VNeID",                  domain: ["gov"],                   category: "gov",      icon: "🪪", isDefault: true,  isSensitive: false },
  { id: "tax_mcp",           name: "Cục Thuế",            description: "Tra cứu MST, khai thuế TNCN",                   domain: ["gov"],                   category: "gov",      icon: "📑", isDefault: true,  isSensitive: false },
  { id: "dvcqg_submit",      name: "DVCQG Submit",        description: "Nộp hồ sơ lên Cổng dịch vụ công quốc gia",      domain: ["gov"],                   category: "gov",      icon: "📮", isDefault: false, isSensitive: true  },
  { id: "bhxh_mcp",          name: "BHXH",                description: "Tra cứu bảo hiểm xã hội",                       domain: ["gov"],                   category: "gov",      icon: "🏥", isDefault: false, isSensitive: false },

  { id: "pdf_extract",       name: "PDF Extract",         description: "Trích xuất text, bảng từ PDF",                  domain: ["document"],              category: "data",     icon: "📄", isDefault: true  },
  { id: "qdrant_search",     name: "Vector Search",       description: "Semantic search trong Qdrant vector store",      domain: ["document", "enterprise"],category: "data",     icon: "🔍", isDefault: true  },
  { id: "docling_ocr",       name: "Docling OCR",         description: "OCR tiếng Việt cho PDF scan bằng PaddleOCR",    domain: ["document"],              category: "data",     icon: "👁", isDefault: false },
]

export const AGENT_SKILLS: AgentSkill[] = [
  {
    id: "skill-chart",
    name: "Kèm biểu đồ",
    description: "Luôn tạo chart cho số liệu",
    icon: "📊",
    color: "bg-blue-500/10 text-blue-500 border-blue-500/30",
    domain: ["stockllm", "enterprise", "forecast"],
    category: "output",
    promptTemplate: "Always include a chart or visualization when presenting numerical data. Use bar charts for comparisons, line charts for trends.",
  },
  {
    id: "skill-table",
    name: "Định dạng bảng",
    description: "Trình bày dữ liệu dạng bảng",
    icon: "📋",
    color: "bg-emerald-500/10 text-emerald-500 border-emerald-500/30",
    domain: ["stockllm", "enterprise", "gov", "forecast", "document"],
    category: "output",
    promptTemplate: "Present all structured data in markdown tables with clear headers. Include units in column headers.",
  },
  {
    id: "skill-pdf",
    name: "Tạo báo cáo PDF",
    description: "Xuất kết quả thành file báo cáo",
    icon: "📄",
    color: "bg-red-500/10 text-red-500 border-red-500/30",
    domain: ["enterprise", "stockllm"],
    category: "output",
    promptTemplate: "After analysis, generate a formal PDF report artifact with executive summary, key findings, and data tables.",
  },
  {
    id: "skill-cite",
    name: "Trích dẫn nguồn",
    description: "Mọi số liệu phải có nguồn rõ ràng",
    icon: "🔗",
    color: "bg-violet-500/10 text-violet-500 border-violet-500/30",
    domain: ["stockllm", "enterprise", "gov", "forecast", "document"],
    category: "output",
    promptTemplate: "Every statistic or data point must include a citation badge showing the data source (MCP tool name, table name, or document).",
  },
  {
    id: "skill-concise",
    name: "Ngắn gọn",
    description: "Tối đa 3 bullet points",
    icon: "⚡",
    color: "bg-amber-500/10 text-amber-500 border-amber-500/30",
    domain: ["stockllm", "enterprise", "gov", "forecast", "document"],
    category: "behavior",
    promptTemplate: "Keep responses concise. Maximum 3 bullet points. Lead with the most important insight. No filler phrases.",
  },
  {
    id: "skill-detailed",
    name: "Chi tiết đầy đủ",
    description: "Giải thích kỹ từng bước",
    icon: "🔍",
    color: "bg-cyan-500/10 text-cyan-500 border-cyan-500/30",
    domain: ["stockllm", "enterprise", "gov", "forecast", "document"],
    category: "behavior",
    promptTemplate: "Provide detailed explanations. Show step-by-step reasoning. Include methodology and assumptions.",
  },
  {
    id: "skill-hitl",
    name: "Xác nhận trước",
    description: "Hỏi trước khi thực hiện action",
    icon: "⚠️",
    color: "bg-orange-500/10 text-orange-500 border-orange-500/30",
    domain: ["gov", "enterprise"],
    category: "behavior",
    promptTemplate: "Always ask for human confirmation before executing any write operations, form submissions, or irreversible actions.",
  },
  {
    id: "skill-compare",
    name: "So sánh",
    description: "Luôn so sánh với kỳ trước",
    icon: "↔️",
    color: "bg-pink-500/10 text-pink-500 border-pink-500/30",
    domain: ["stockllm", "enterprise", "forecast"],
    category: "analysis",
    promptTemplate: "Always compare current data with the previous period (day-over-day, week-over-week, or quarter-over-quarter). Show percentage change.",
  },
  {
    id: "skill-risk",
    name: "Phân tích rủi ro",
    description: "Nêu rõ rủi ro và hạn chế",
    icon: "🛡",
    color: "bg-red-500/10 text-red-400 border-red-500/30",
    domain: ["stockllm", "forecast", "enterprise"],
    category: "analysis",
    promptTemplate: "Include a risk analysis section. Highlight key risks, data limitations, and confidence intervals. Do not overstate certainty.",
  },
  {
    id: "skill-action",
    name: "Đề xuất hành động",
    description: "Kết luận bằng 3 action items",
    icon: "🎯",
    color: "bg-green-500/10 text-green-500 border-green-500/30",
    domain: ["stockllm", "enterprise", "forecast"],
    category: "analysis",
    promptTemplate: "End every response with exactly 3 concrete action items the user can take immediately based on the analysis.",
  },
  {
    id: "skill-vi",
    name: "Tiếng Việt",
    description: "Luôn trả lời tiếng Việt",
    icon: "🇻🇳",
    color: "bg-red-500/10 text-red-400 border-red-500/30",
    domain: ["stockllm", "enterprise", "gov", "forecast", "document"],
    category: "language",
    promptTemplate: "Always respond in Vietnamese. Use professional Vietnamese financial and business terminology.",
  },
  {
    id: "skill-en",
    name: "English",
    description: "Respond in English only",
    icon: "🇺🇸",
    color: "bg-blue-500/10 text-blue-400 border-blue-500/30",
    domain: ["stockllm", "enterprise", "forecast"],
    category: "language",
    promptTemplate: "Always respond in English. Use professional financial and business English terminology.",
  },
]

const STEEL_SCHEMA: SchemaTable[] = [
  {
    name: "hose_foreign_flow",
    rowCount: "520,480",
    columns: [
      { name: "id",                type: "BIGINT",        nullable: false, pk: true },
      { name: "ticker",            type: "VARCHAR(10)",   nullable: false,            description: "Mã cổ phiếu" },
      { name: "trade_date",        type: "DATE",          nullable: false },
      { name: "foreign_buy_vol",   type: "BIGINT",        nullable: true,             description: "KL ngoại mua" },
      { name: "foreign_sell_vol",  type: "BIGINT",        nullable: true,             description: "KL ngoại bán" },
      { name: "foreign_buy_value", type: "DECIMAL(18,2)", nullable: true,             description: "Giá trị ngoại mua (VNĐ)" },
      { name: "foreign_sell_value",type: "DECIMAL(18,2)", nullable: true,             description: "Giá trị ngoại bán (VNĐ)" },
      { name: "created_at",        type: "TIMESTAMP",     nullable: false },
    ],
  },
  {
    name: "company_master",
    rowCount: "1,680",
    columns: [
      { name: "ticker",       type: "VARCHAR(10)",   nullable: false, pk: true },
      { name: "company_name", type: "VARCHAR(255)",  nullable: false },
      { name: "sector",       type: "VARCHAR(100)",  nullable: true,  description: "Ngành (STEEL, BANK...)" },
      { name: "market_cap",   type: "DECIMAL(20,2)", nullable: true },
      { name: "last_price",   type: "DECIMAL(12,2)", nullable: true },
      { name: "rsi_14d",      type: "DECIMAL(5,2)",  nullable: true,  description: "RSI 14 ngày" },
      { name: "exchange",     type: "VARCHAR(10)",   nullable: false, description: "HOSE / HNX / UPCOM" },
    ],
  },
  {
    name: "steel_prices",
    rowCount: "18,240",
    columns: [
      { name: "id",         type: "BIGINT",        nullable: false, pk: true },
      { name: "ticker",     type: "VARCHAR(10)",   nullable: false, fk: "company_master.ticker" },
      { name: "trade_date", type: "DATE",          nullable: false },
      { name: "open",       type: "DECIMAL(12,2)", nullable: true },
      { name: "high",       type: "DECIMAL(12,2)", nullable: true },
      { name: "low",        type: "DECIMAL(12,2)", nullable: true },
      { name: "close",      type: "DECIMAL(12,2)", nullable: true },
      { name: "volume",     type: "BIGINT",        nullable: true },
    ],
  },
]

export const MOCK_ARTIFACTS: Artifact[] = [
  {
    id: "art-graph",
    type: "graph",
    title: "Knowledge Graph — Ngành Thép",
    messageId: "m3",
    createdAt: new Date(Date.now() - 6 * 60 * 1000),
    content: "",
    graphData: {
      nodes: [
        { id: "n1",  position: { x: 300, y: 20  }, data: { label: "Snowflake DW",    nodeType: "database", description: "Data warehouse chính",      status: "active",   properties: { engine: "Snowflake", rows: "42M" } } },
        { id: "n2",  position: { x: 80,  y: 140 }, data: { label: "steel_prices",    nodeType: "table",    description: "Giá thép OHLCV ngày",       status: "active",   properties: { rows: "18,240", updated: "live" } } },
        { id: "n3",  position: { x: 310, y: 140 }, data: { label: "company_master",  nodeType: "table",    description: "Danh mục công ty niêm yết",  status: "active",   properties: { rows: "1,680",  updated: "daily" } } },
        { id: "n4",  position: { x: 530, y: 140 }, data: { label: "foreign_flow",    nodeType: "table",    description: "Ngoại mua/bán ròng",         status: "active",   properties: { rows: "520K",   updated: "realtime" } } },
        { id: "n5",  position: { x: 0,   y: 280 }, data: { label: "HPG",             nodeType: "entity",   description: "Hòa Phát Group",             status: "active",   properties: { sector: "Steel", cap: "125 tỷ" } } },
        { id: "n6",  position: { x: 160, y: 280 }, data: { label: "HSG",             nodeType: "entity",   description: "Hoa Sen Group",              status: "active",   properties: { sector: "Steel", cap: "18 tỷ" } } },
        { id: "n7",  position: { x: 320, y: 280 }, data: { label: "NKG",             nodeType: "entity",   description: "Nam Kim Group",              status: "active",   properties: { sector: "Steel", cap: "8 tỷ" } } },
        { id: "n8",  position: { x: 560, y: 40  }, data: { label: "HOSE Realtime",   nodeType: "stream",   description: "WebSocket tick data",        status: "active",   properties: { latency: "80ms", protocol: "WS" } } },
        { id: "n9",  position: { x: 560, y: 200 }, data: { label: "YFinance API",    nodeType: "api",      description: "OHLCV & fundamentals",       status: "active",   properties: { rateLimit: "2k/hr" } } },
        { id: "n10", position: { x: 80,  y: 400 }, data: { label: "Báo cáo thường niên HPG 2024", nodeType: "document", description: "PDF 147 trang", status: "active",   properties: { pages: "147", lang: "vi" } } },
        { id: "n11", position: { x: 280, y: 400 }, data: { label: "Chart ngoại mua ròng", nodeType: "image", description: "Biểu đồ bar chart",      status: "active",   properties: { format: "PNG", size: "840×480" } } },
        { id: "n12", position: { x: 480, y: 400 }, data: { label: "Neo4j Graph DB",  nodeType: "database", description: "Knowledge graph Neo4j",      status: "active",   properties: { nodes: "24K",   edges: "87K" } } },
      ],
      edges: [
        { id: "e1",  source: "n1",  target: "n2",  label: "contains",     animated: false },
        { id: "e2",  source: "n1",  target: "n3",  label: "contains",     animated: false },
        { id: "e3",  source: "n1",  target: "n4",  label: "contains",     animated: false },
        { id: "e4",  source: "n2",  target: "n5",  label: "tracks",       animated: false },
        { id: "e5",  source: "n2",  target: "n6",  label: "tracks",       animated: false },
        { id: "e6",  source: "n2",  target: "n7",  label: "tracks",       animated: false },
        { id: "e7",  source: "n3",  target: "n5",  label: "describes",    animated: false },
        { id: "e8",  source: "n3",  target: "n6",  label: "describes",    animated: false },
        { id: "e9",  source: "n4",  target: "n5",  label: "flow data",    animated: true  },
        { id: "e10", source: "n8",  target: "n4",  label: "feeds",        animated: true  },
        { id: "e11", source: "n9",  target: "n2",  label: "syncs",        animated: false },
        { id: "e12", source: "n5",  target: "n10", label: "has report",   animated: false },
        { id: "e13", source: "n4",  target: "n11", label: "visualized as",animated: false },
        { id: "e14", source: "n5",  target: "n12", label: "in graph",     animated: false },
        { id: "e15", source: "n6",  target: "n12", label: "in graph",     animated: false },
      ],
    },
  },
  {
    id: "art-1",
    type: "markdown",
    title: "Báo cáo ngoại mua ròng ngành thép",
    messageId: "m3",
    createdAt: new Date(Date.now() - 7 * 60 * 1000),
    content: `# Báo cáo Ngoại Mua Ròng — Ngành Thép
**Ngày:** ${new Date().toLocaleDateString("vi-VN")}
**Nguồn:** HOSE Realtime · Neo4j Industry Graph

---

## Tóm tắt
Ngoại mua ròng toàn ngành thép hôm nay đạt **+57 tỷ VNĐ**,
tập trung chủ yếu vào 2 mã đầu ngành.

## Chi tiết theo mã

| Mã CP | Mua ròng | Giá hiện tại | KL khớp |
|-------|----------|--------------|---------|
| HPG   | +45 tỷ  | 26,500đ      | 12.4M   |
| HSG   | +12 tỷ  | 14,200đ      | 8.1M    |
| NKG   | +3.5 tỷ | 18,700đ      | 2.3M    |
| TIS   | -2 tỷ   | 9,800đ       | 1.1M    |

## Phân tích nguyên nhân

1. **USD giảm 0.3%** so với VNĐ → nhóm tài nguyên được hưởng lợi
2. **Giá thép thế giới tăng 2%** → kỳ vọng lợi nhuận Q3 cải thiện
3. **RSI HPG = 58** → chưa overbought, room tăng còn

## Khuyến nghị
> Dòng tiền ngoại đang tích cực. Theo dõi phiên sáng mai
> để xác nhận xu hướng. Ngưỡng hỗ trợ HPG: 25,800đ.`,
  },
  {
    id: "art-2",
    type: "html",
    title: "Dashboard doanh thu Q2 theo vùng",
    messageId: "m8",
    createdAt: new Date(Date.now() - 23 * 60 * 60 * 1000),
    content: `<div style="font-family:sans-serif;padding:20px;max-width:600px">
  <h2 style="font-size:18px;font-weight:600;margin-bottom:16px">
    Doanh thu Q2/2025 theo vùng miền
  </h2>
  <div style="display:flex;flex-direction:column;gap:12px">
    <div>
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <span style="font-size:13px">Miền Nam</span>
        <span style="font-size:13px;font-weight:600;color:#10b981">82.9 tỷ (+16.1%)</span>
      </div>
      <div style="background:#e5e7eb;border-radius:4px;height:28px;overflow:hidden">
        <div style="background:#10b981;height:100%;width:83%;display:flex;align-items:center;padding-left:8px">
          <span style="color:white;font-size:11px;font-weight:500">83%</span>
        </div>
      </div>
    </div>
    <div>
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <span style="font-size:13px">Miền Bắc</span>
        <span style="font-size:13px;font-weight:600;color:#3b82f6">48.1 tỷ (+13.7%)</span>
      </div>
      <div style="background:#e5e7eb;border-radius:4px;height:28px;overflow:hidden">
        <div style="background:#3b82f6;height:100%;width:48%;display:flex;align-items:center;padding-left:8px">
          <span style="color:white;font-size:11px;font-weight:500">48%</span>
        </div>
      </div>
    </div>
    <div>
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <span style="font-size:13px">Miền Trung</span>
        <span style="font-size:13px;font-weight:600;color:#ef4444">17.2 tỷ (-7.5%)</span>
      </div>
      <div style="background:#e5e7eb;border-radius:4px;height:28px;overflow:hidden">
        <div style="background:#ef4444;height:100%;width:17%;display:flex;align-items:center;padding-left:8px">
          <span style="color:white;font-size:11px;font-weight:500">17%</span>
        </div>
      </div>
    </div>
  </div>
  <p style="font-size:11px;color:#9ca3af;margin-top:16px">
    Nguồn: Snowflake DW · dbt:regional_revenue · Cập nhật: ${new Date().toLocaleString("vi-VN")}
  </p>
</div>`,
  },
  {
    id: "art-3",
    type: "code",
    title: "SQL query — ngoại mua ròng theo ngành",
    language: "sql",
    messageId: "m3",
    createdAt: new Date(Date.now() - 8 * 60 * 1000),
    content: `-- Ngoại mua ròng theo ngành hôm nay
-- Generated by Analysis Agent · ${new Date().toLocaleDateString("vi-VN")}

SELECT
  c.sector,
  c.ticker,
  c.company_name,
  SUM(f.foreign_buy_value - f.foreign_sell_value) AS net_foreign_value,
  SUM(f.foreign_buy_volume)                        AS buy_volume,
  SUM(f.foreign_sell_volume)                       AS sell_volume,
  c.last_price,
  c.rsi_14d
FROM hose_foreign_flow f
JOIN company_master c ON f.ticker = c.ticker
WHERE
  f.trade_date = CURRENT_DATE
  AND c.sector = 'STEEL'
GROUP BY
  c.sector, c.ticker, c.company_name,
  c.last_price, c.rsi_14d
ORDER BY
  net_foreign_value DESC;

-- Result: 5 rows, 0.23s execution time`,
  },
  {
    id: "art-image",
    type: "image",
    title: "Biểu đồ ngoại mua ròng — snapshot",
    messageId: "m3",
    createdAt: new Date(Date.now() - 5 * 60 * 1000),
    content: "",
    imageUrl: "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=900&q=80",
    imageAlt: "Stock market chart — ngoại mua ròng ngành thép",
  },
  {
    id: "art-db",
    type: "table",
    title: "Schema — Snowflake DW (ngành thép)",
    messageId: "m3",
    createdAt: new Date(Date.now() - 4 * 60 * 1000),
    content: "",
    schemaData: STEEL_SCHEMA,
  },
]

export const MOCK_CONVERSATIONS: Conversation[] = [
  {
    id: "conv-1",
    title: "Ngoại mua ròng ngành thép hôm nay",
    domain: "stockllm",
    createdAt: new Date(Date.now() - 12 * 60 * 1000),
    updatedAt: new Date(Date.now() - 5 * 60 * 1000),
    activeAgents: ["Graph RAG", "Realtime MCP", "Analysis"],
    messages: [
      {
        id: "m1", role: "user",
        content: "Ngoại mua ròng thép hôm nay thế nào? Vì sao?",
        timestamp: new Date(Date.now() - 10 * 60 * 1000),
      },
      {
        id: "m2", role: "assistant", content: "",
        timestamp: new Date(Date.now() - 9 * 60 * 1000),
        agentTrace: [
          { id: "s1", workerName: "Graph RAG", description: "Truy xuất knowledge graph ngành thép — HPG, HSG, NKG", status: "done", durationMs: 2100, detail: "MATCH (i:Industry {code:'STEEL'})-[:INCLUDES]->(c:Company) RETURN c" },
          { id: "s2", workerName: "Realtime MCP", description: "Lấy dữ liệu hose_foreign_flow realtime", status: "done", durationMs: 1400, detail: "MCP tool: hose_foreign_flow({ date: 'today', sector: 'steel' })" },
          { id: "s3", workerName: "Analysis Agent", description: "Tổng hợp & phân tích nguyên nhân", status: "running" },
          { id: "s4", workerName: "Visualization", description: "Render biểu đồ", status: "pending" },
        ],
      },
      {
        id: "m3", role: "assistant",
        content: "Ngoại mua ròng **HPG +45 tỷ**, **HSG +12 tỷ** — tổng ngành thép **+57 tỷ VNĐ** hôm nay.\n\nNguyên nhân: USD giảm 0.3% và giá thép thế giới tăng 2% kéo nhóm tài nguyên cơ bản. RSI HPG ở 58 — chưa overbought, dòng tiền ngoại có thể tiếp tục.",
        timestamp: new Date(Date.now() - 7 * 60 * 1000),
        chartData: {
          type: "bar",
          title: "Ngoại mua ròng ngành thép (tỷ VNĐ)",
          labels: ["HPG", "HSG", "NKG", "TIS", "SMC"],
          values: [45, 12, 3.5, -2, -0.8],
          unit: "tỷ VNĐ",
        },
        citations: [
          { source: "hose_foreign_flow", type: "mcp" },
          { source: "neo4j:Industry.Steel", type: "neo4j" },
          { source: "macro_data:USD/VND", type: "mcp" },
        ],
      },
      {
        id: "m4", role: "assistant", content: "",
        timestamp: new Date(Date.now() - 6 * 60 * 1000),
        hitl: {
          title: "Xác nhận hành động",
          description: "Tạo báo cáo PDF và gửi email cho 12 người theo dõi trong danh sách \"Steel Watchers\"?",
          actions: [
            { id: "confirm", label: "Xác nhận gửi", variant: "confirm" },
            { id: "preview", label: "Xem trước", variant: "preview" },
            { id: "cancel", label: "Huỷ", variant: "cancel" },
          ],
          expiresAt: new Date(Date.now() + 8 * 60 * 1000),
        },
      },
    ],
  },
  {
    id: "conv-2",
    title: "Đăng ký kinh doanh hộ cá thể HCM",
    domain: "gov",
    createdAt: new Date(Date.now() - 60 * 60 * 1000),
    updatedAt: new Date(Date.now() - 58 * 60 * 1000),
    messages: [
      { id: "m5", role: "user", content: "Tôi muốn đăng ký hộ kinh doanh cá thể tại quận 1, TP.HCM.", timestamp: new Date(Date.now() - 60 * 60 * 1000) },
      { id: "m6", role: "assistant", content: "Đã xác thực thành công qua VNeID. Để đăng ký hộ kinh doanh cá thể tại Quận 1 bạn cần:\n\n1. **CCCD/CMND** bản gốc ✅\n2. **Giấy CN địa điểm** — hợp đồng thuê mặt bằng\n3. **Đơn đăng ký** — agent sẽ điền tự động\n\nBạn đã có hợp đồng thuê mặt bằng chưa?", timestamp: new Date(Date.now() - 59 * 60 * 1000), citations: [{ source: "vneid_mcp", type: "mcp" }, { source: "neo4j:BusinessReg", type: "neo4j" }] },
    ],
  },
  {
    id: "conv-3",
    title: "Báo cáo doanh thu Q2 theo vùng",
    domain: "enterprise",
    createdAt: new Date(Date.now() - 24 * 60 * 60 * 1000),
    updatedAt: new Date(Date.now() - 23 * 60 * 60 * 1000),
    messages: [
      { id: "m7", role: "user", content: "Tạo báo cáo doanh thu Q2 chia theo 3 miền, so với Q1", timestamp: new Date(Date.now() - 24 * 60 * 60 * 1000) },
      { id: "m8", role: "assistant", content: "Đã query Snowflake. Dưới đây là doanh thu Q2 vs Q1:", timestamp: new Date(Date.now() - 23 * 60 * 60 * 1000),
        tableData: { headers: ["Vùng", "Q1 (tỷ)", "Q2 (tỷ)", "Tăng trưởng"], rows: [["Miền Bắc", 42.3, 48.1, "+13.7%"], ["Miền Trung", 18.6, 17.2, "-7.5%"], ["Miền Nam", 71.4, 82.9, "+16.1%"]] },
        chartData: { type: "bar", title: "Doanh thu theo vùng Q1 vs Q2", labels: ["Miền Bắc", "Miền Trung", "Miền Nam"], values: [48.1, 17.2, 82.9], unit: "tỷ VNĐ" },
        citations: [{ source: "snowflake:revenue_q2", type: "sql" }, { source: "dbt:regional_revenue", type: "sql" }] },
    ],
  },
  {
    id: "conv-4", title: "Phân tích hợp đồng bảo hiểm PDF", domain: "document",
    createdAt: new Date(Date.now() - 2 * 86400000), updatedAt: new Date(Date.now() - 2 * 86400000),
    messages: [
      { id: "m9", role: "user", content: "Đọc hợp đồng và tóm tắt điều khoản quan trọng nhất", timestamp: new Date(Date.now() - 2 * 86400000) },
      { id: "m10", role: "assistant", content: "Đã đọc 47 trang. Điều khoản quan trọng:\n\n**Bảo hiểm:** Tai nạn cá nhân, ốm đau, nằm viện\n**Bồi thường tối đa:** 500 triệu/năm\n**Thời gian chờ:** 30 ngày bệnh thông thường, 180 ngày bệnh mãn tính\n**Exclusions:** Không cover thể thao mạo hiểm", timestamp: new Date(Date.now() - 2 * 86400000) },
    ],
  },
  {
    id: "conv-5", title: "Dự báo doanh thu tháng 7/2025", domain: "forecast",
    createdAt: new Date(Date.now() - 3 * 86400000), updatedAt: new Date(Date.now() - 3 * 86400000),
    messages: [
      { id: "m11", role: "user", content: "Dự báo doanh thu tháng 7 với 3 kịch bản", timestamp: new Date(Date.now() - 3 * 86400000) },
      { id: "m12", role: "assistant", content: "**Kịch bản A (Base):** 89.2 tỷ ±8% — Xác suất 65%\n**Kịch bản B (Optimistic):** 102.4 tỷ ±5% — Xác suất 20%\n**Kịch bản C (Pessimistic):** 74.1 tỷ ±12% — Xác suất 15%\n\nDriver chính: mùa hè tăng tiêu dùng + khuyến mãi Q3 kick-in tuần 2.", timestamp: new Date(Date.now() - 3 * 86400000) },
    ],
  },
]

export const MOCK_DATA_SOURCES: DataSource[] = [
  { id: "ds1", name: "Snowflake DW",   type: "warehouse", status: "connected" },
  { id: "ds2", name: "Neo4j Graph",    type: "graph",     status: "connected" },
  { id: "ds3", name: "HOSE Realtime",  type: "realtime",  status: "connected" },
  { id: "ds4", name: "Qdrant Vector",  type: "document",  status: "slow" },
]

export const MOCK_ACTIVE_AGENTS: ActiveAgent[] = [
  { id: "a1", name: "Graph RAG",      description: "Truy xuất knowledge graph ngành", status: "done" },
  { id: "a2", name: "Realtime MCP",   description: "Dữ liệu thị trường live",         status: "done" },
  { id: "a3", name: "Analysis Agent", description: "Tổng hợp & phân tích",            status: "running" },
  { id: "a4", name: "Visualization",  description: "Render biểu đồ Plotly",           status: "pending" },
]

export const MOCK_SESSION_MEMORY = [
  "Đang theo dõi HPG, HSG, NKG",
  "Quan tâm: ngoại mua ròng, USD/VNĐ, giá thép",
  "Đã tạo 1 báo cáo trong phiên",
]

const MOCK_FILES_PROJ1: ProjectFile[] = [
  { id: "f1", name: "DataAgent_Platform_Research.docx", type: "docx", lines: 660,  sizeKb: 84,  uploadedAt: new Date("2025-05-12"), status: "ready" },
  { id: "f2", name: "Research - Đức Long.docx",         type: "docx", lines: 5538, sizeKb: 210, uploadedAt: new Date("2025-05-14"), status: "ready" },
  { id: "f3", name: "Research #1_Agent_QuanLe.docx",    type: "docx", lines: 1094, sizeKb: 96,  uploadedAt: new Date("2025-05-15"), status: "indexing" },
  { id: "f4", name: "Agentic_Design_Patterns.pdf",      type: "pdf",  lines: 15183,sizeKb: 420, uploadedAt: new Date("2025-05-16"), status: "ready" },
  { id: "f5", name: "PROJECT_SUMMARY.md",               type: "md",   lines: 82,   sizeKb: 6,   uploadedAt: new Date("2025-05-18"), status: "ready" },
]

const MOCK_FILES_PROJ2: ProjectFile[] = [
  { id: "f6", name: "Q2_Revenue_Report.xlsx",  type: "other", sizeKb: 128, uploadedAt: new Date("2025-05-05"), status: "ready" },
  { id: "f7", name: "Forecast_Model_v2.csv",   type: "csv",   lines: 2400, sizeKb: 56,  uploadedAt: new Date("2025-05-08"), status: "ready" },
]

export const MOCK_PROJECTS: Project[] = [
  {
    id: "proj-1",
    name: "Phân tích thị trường chứng khoán",
    description: "Theo dõi dòng vốn ngoại và cổ phiếu thép",
    color: "bg-amber-600",
    conversationIds: ["conv-1"],
    instructions: "Luôn trích dẫn nguồn dữ liệu khi đưa ra nhận định. Ưu tiên phân tích kỹ thuật kết hợp dòng tiền ngoại. Trả lời bằng tiếng Việt.",
    files: MOCK_FILES_PROJ1,
    createdAt: new Date("2025-05-10"),
    updatedAt: new Date("2025-05-20"),
  },
  {
    id: "proj-2",
    name: "Báo cáo doanh nghiệp Q2",
    description: "Phân tích doanh thu và dự báo theo vùng",
    color: "bg-emerald-700",
    conversationIds: ["conv-3", "conv-5"],
    instructions: "Tập trung vào số liệu so sánh YoY. Trình bày kết quả dưới dạng bảng khi có thể.",
    files: MOCK_FILES_PROJ2,
    createdAt: new Date("2025-05-01"),
    updatedAt: new Date("2025-05-18"),
  },
]
