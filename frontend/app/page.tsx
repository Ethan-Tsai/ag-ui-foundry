"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { CopilotKit, useCoAgent } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import {
  AlertCircle,
  Archive,
  BadgeDollarSign,
  Bot,
  ChartSpline,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cpu,
  Database,
  File,
  FileJson,
  FileSearch,
  FileSpreadsheet,
  FileText,
  FileUp,
  Handshake,
  LayoutGrid,
  Loader2,
  Menu,
  MessageSquare,
  Network,
  Paperclip,
  PanelLeftClose,
  PanelLeftOpen,
  Scale,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRoundCog,
  Users,
  Wrench,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import "@copilotkit/react-ui/styles.css";
import "./style.css";

interface FoundryAgent {
  name: string;
  description: string;
  status?: string;
  tags?: string[];
}

interface AgentInfo {
  name: string;
  description: string;
  welcomeMessage: string;
  starterPrompts: string[];
}

interface AgentGroup {
  category: string;
  icon: LucideIcon;
  agents: FoundryAgent[];
}

interface DataPoint {
  metric_name: string;
  value: string;
  context: string;
}

interface PowerBIState {
  dashboard_context: string;
  current_insights: string;
  data_points: DataPoint[];
}

interface RuntimeFile {
  filename: string;
  fileId: string;
  typeLabel: string;
  Icon: LucideIcon;
}

interface GeneratedFileItem {
  filename: string;
  href: string;
  type?: string;
  size?: string;
  createdAt?: string;
}

type UploadState = "idle" | "uploading" | "success" | "error";
type StatusVariant = "ready" | "draft" | "error" | "running" | "neutral";

const CATEGORY_RULES = [
  {
    category: "人資營運",
    icon: Users,
    keywords: ["hr", "leave", "timesheet", "onboard", "admin", "operations", "people"],
  },
  {
    category: "資料與財務",
    icon: ChartSpline,
    keywords: ["finance", "data", "report", "revenue", "pbi", "dashboard", "bi", "analytics"],
  },
  {
    category: "平台支援",
    icon: Cpu,
    keywords: ["it", "support", "infra", "chatbot", "sharepoint", "integration", "platform"],
  },
] satisfies Array<{ category: string; icon: LucideIcon; keywords: string[] }>;

const AGENT_ICON_RULES = [
  { keywords: ["hr", "leave", "timesheet", "people", "admin"], icon: UserRoundCog, tone: "tone-people" },
  { keywords: ["finance", "revenue", "budget", "cost", "payment"], icon: BadgeDollarSign, tone: "tone-finance" },
  { keywords: ["data", "report", "dashboard", "bi", "pbi", "analytics"], icon: Database, tone: "tone-data" },
  { keywords: ["risk", "compliance", "legal", "policy", "audit"], icon: Scale, tone: "tone-risk" },
  { keywords: ["security", "soc", "threat", "identity"], icon: ShieldCheck, tone: "tone-security" },
  { keywords: ["support", "it", "infra", "ticket", "platform"], icon: Wrench, tone: "tone-platform" },
  { keywords: ["search", "knowledge", "library", "document"], icon: FileSearch, tone: "tone-knowledge" },
] satisfies Array<{ keywords: string[]; icon: LucideIcon; tone: string }>;

const INITIAL_POWERBI_STATE: PowerBIState = {
  dashboard_context: "",
  current_insights: "",
  data_points: [],
};

const FALLBACK_PROMPTS = [
  "請整理目前資料的重點摘要",
  "比較本月與上月的差異",
  "找出異常點並說明可能原因",
];

function categorizeAgents(agents: FoundryAgent[], routerName: string): { router: FoundryAgent | null; groups: AgentGroup[] } {
  const router = agents.find((agent) => agent.name === routerName) ?? (agents.length > 0 ? agents[0] : null);
  const rest = agents.filter((agent) => agent.name !== router?.name);
  const groups: AgentGroup[] = [];
  const used = new Set<string>();

  for (const rule of CATEGORY_RULES) {
    const matched = rest.filter((agent) => {
      const corpus = `${agent.name} ${agent.description}`.toLowerCase();
      return rule.keywords.some((keyword) => corpus.includes(keyword));
    });

    if (matched.length > 0) {
      groups.push({ category: rule.category, icon: rule.icon, agents: matched });
      matched.forEach((agent) => used.add(agent.name));
    }
  }

  const uncategorized = rest.filter((agent) => !used.has(agent.name));
  if (uncategorized.length > 0) {
    groups.push({ category: "一般代理", icon: LayoutGrid, agents: uncategorized });
  }

  return { router, groups };
}

function getAgentVisual(agent: FoundryAgent, isRouter: boolean): { icon: LucideIcon; tone: string } {
  if (isRouter) return { icon: Handshake, tone: "tone-router" };
  const corpus = `${agent.name} ${agent.description}`.toLowerCase();
  const matched = AGENT_ICON_RULES.find((rule) => rule.keywords.some((keyword) => corpus.includes(keyword)));
  return matched ? { icon: matched.icon, tone: matched.tone } : { icon: Bot, tone: "tone-default" };
}

function getAgentCategory(agent: FoundryAgent, isRouter: boolean): string {
  if (isRouter) return "編排代理";
  const corpus = `${agent.name} ${agent.description}`.toLowerCase();
  return CATEGORY_RULES.find((rule) => rule.keywords.some((keyword) => corpus.includes(keyword)))?.category ?? "一般代理";
}

function getCapabilityTags(agent: FoundryAgent, isRouter: boolean): string[] {
  if (Array.isArray(agent.tags) && agent.tags.length > 0) return agent.tags.slice(0, 3);
  if (isRouter) return ["Routing", "Foundry", "AG-UI"];
  const corpus = `${agent.name} ${agent.description}`.toLowerCase();
  const tags: string[] = [];
  if (/(data|report|dashboard|analytics|pbi|bi)/.test(corpus)) tags.push("Data");
  if (/(file|document|pdf|excel|csv|knowledge|search)/.test(corpus)) tags.push("Files");
  if (/(finance|budget|revenue|cost)/.test(corpus)) tags.push("Finance");
  if (/(hr|people|leave|timesheet)/.test(corpus)) tags.push("HR");
  if (/(support|ticket|it|infra|platform)/.test(corpus)) tags.push("Support");
  return (tags.length > 0 ? tags : ["Chat", "Tools"]).slice(0, 3);
}

function getStatusVariant(status?: string): StatusVariant {
  const value = (status ?? "ready").toLowerCase();
  if (value.includes("error") || value.includes("fail")) return "error";
  if (value.includes("draft")) return "draft";
  if (value.includes("run") || value.includes("loading") || value.includes("process")) return "running";
  if (value.includes("ready") || value.includes("active")) return "ready";
  return "neutral";
}

function getStatusLabel(status?: string): string {
  const variant = getStatusVariant(status);
  if (variant === "error") return "Error";
  if (variant === "draft") return "Draft";
  if (variant === "running") return "Running";
  if (variant === "ready") return "Ready";
  return status || "Ready";
}

function getFileMeta(filename: string): { typeLabel: string; Icon: LucideIcon } {
  const lower = filename.toLowerCase();
  if (/\.(xlsx|xls|csv)$/.test(lower)) return { typeLabel: "表格", Icon: FileSpreadsheet };
  if (/\.json$/.test(lower)) return { typeLabel: "JSON", Icon: FileJson };
  if (/\.(txt|md|log)$/.test(lower)) return { typeLabel: "文字", Icon: FileText };
  if (/\.(zip|parquet)$/.test(lower)) return { typeLabel: "封存", Icon: Archive };
  if (/\.pdf$/.test(lower)) return { typeLabel: "PDF", Icon: FileText };
  return { typeLabel: "檔案", Icon: File };
}

export default function ProjectPage() {
  useSuppressConsoleNoise();

  const [agents, setAgents] = useState<FoundryAgent[]>([]);
  const [loadingAgents, setLoadingAgents] = useState(true);
  const [agentLoadError, setAgentLoadError] = useState("");
  const [selectedAgent, setSelectedAgent] = useState("");
  const [agentInfo, setAgentInfo] = useState<AgentInfo | null>(null);
  const [loadingInfo, setLoadingInfo] = useState(false);
  const [agentInfoError, setAgentInfoError] = useState("");
  const [searchQ, setSearchQ] = useState("");
  const [isDesktopCompact, setIsDesktopCompact] = useState(false);
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [isRunContextOpen, setIsRunContextOpen] = useState(true);
  const [uploadedFileIds, setUploadedFileIds] = useState<Record<string, string>>({});
  const [isUploadingFiles, setIsUploadingFiles] = useState(false);
  const [uploadStatus, setUploadStatus] = useState("");
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [isFileDragActive, setIsFileDragActive] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        setAgentLoadError("");
        const response = await fetch("/api/agents");
        if (!response.ok) throw new Error(`代理清單讀取失敗 (${response.status})`);
        const payload = await response.json();
        if (payload.agents?.length) {
          setAgents(payload.agents);
          setSelectedAgent(payload.agents[0].name);
        } else {
          setAgentLoadError("尚未探索到可用代理。");
        }
      } catch (error) {
        setAgentLoadError(error instanceof Error ? error.message : "代理清單讀取失敗。");
      } finally {
        setLoadingAgents(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedAgent) return;
    let cancelled = false;
    setLoadingInfo(true);
    setAgentInfoError("");

    (async () => {
      try {
        const response = await fetch(`/api/agent-info?agent_name=${encodeURIComponent(selectedAgent)}`);
        if (!response.ok) throw new Error(`代理資訊讀取失敗 (${response.status})`);
        if (!cancelled) setAgentInfo(await response.json());
      } catch (error) {
        if (!cancelled) {
          setAgentInfo(null);
          setAgentInfoError(error instanceof Error ? error.message : "代理資訊讀取失敗。");
        }
      } finally {
        if (!cancelled) setLoadingInfo(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedAgent]);

  const pickAgent = useCallback((name: string) => {
    if (name !== selectedAgent) {
      setSelectedAgent(name);
      setAgentInfo(null);
      setAgentInfoError("");
    }
    setIsMobileNavOpen(false);
  }, [selectedAgent]);

  const removeUploadedFile = useCallback((filename: string) => {
    setUploadedFileIds((current) => {
      const next = { ...current };
      delete next[filename];
      return next;
    });
    setUploadState("success");
    setUploadStatus(`已移除 ${filename}`);
  }, []);

  const clearUploadedFiles = useCallback(() => {
    setUploadedFileIds({});
    setUploadState("success");
    setUploadStatus("已清除所有執行附件。");
  }, []);

  const uploadFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const fileArray = Array.from(files);
    const formData = new FormData();
    fileArray.forEach((file) => formData.append("files", file));

    setIsUploadingFiles(true);
    setUploadState("uploading");
    setUploadStatus(`正在上傳 ${fileArray.length} 個檔案...`);

    try {
      const response = await fetch("/api/upload", { method: "POST", body: formData });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error ?? `上傳失敗 (${response.status})`);

      const uploaded = payload?.uploaded && typeof payload.uploaded === "object"
        ? (payload.uploaded as Record<string, string>)
        : {};
      const failed = Array.isArray(payload?.failed)
        ? (payload.failed as Array<{ filename?: string; error?: string }>)
        : [];

      if (Object.keys(uploaded).length > 0) {
        setUploadedFileIds((current) => ({ ...current, ...uploaded }));
      }

      if (failed.length > 0) {
        const failedNames = failed.map((item) => item.filename || "unknown").join(", ");
        setUploadState(Object.keys(uploaded).length > 0 ? "success" : "error");
        setUploadStatus(`部分檔案上傳失敗：${failedNames}`);
      } else if (Object.keys(uploaded).length > 0) {
        setUploadState("success");
        setUploadStatus(`已附加 ${Object.keys(uploaded).length} 個檔案到下一次執行。`);
      } else {
        setUploadState("error");
        setUploadStatus("沒有檔案完成上傳。");
      }
    } catch (error) {
      setUploadState("error");
      setUploadStatus(error instanceof Error ? error.message : "上傳失敗");
    } finally {
      setIsUploadingFiles(false);
    }
  }, []);

  const filteredAgents = useMemo(() => {
    if (!searchQ.trim()) return agents;
    const query = searchQ.toLowerCase();
    return agents.filter((agent) => agent.name.toLowerCase().includes(query) || agent.description.toLowerCase().includes(query));
  }, [agents, searchQ]);

  const { router, groups } = useMemo(() => categorizeAgents(filteredAgents, agents[0]?.name ?? ""), [agents, filteredAgents]);
  const selectedAgentData = useMemo(() => agents.find((agent) => agent.name === selectedAgent) ?? null, [agents, selectedAgent]);
  const runtimeFiles = useMemo<RuntimeFile[]>(() => Object.entries(uploadedFileIds).map(([filename, fileId]) => {
    const meta = getFileMeta(filename);
    return { filename, fileId, typeLabel: meta.typeLabel, Icon: meta.Icon };
  }), [uploadedFileIds]);

  const runtimeUrl = useMemo(() => {
    const query = new URLSearchParams();
    if (Object.keys(uploadedFileIds).length > 0) query.set("fileIds", JSON.stringify(uploadedFileIds));
    const encoded = query.toString();
    return encoded ? `/api/copilotkit/default?${encoded}` : "/api/copilotkit/default";
  }, [uploadedFileIds]);

  const starterPrompts = agentInfo?.starterPrompts?.length ? agentInfo.starterPrompts : FALLBACK_PROMPTS;
  const activeTitle = agentInfo?.name || selectedAgentData?.name || selectedAgent;
  const activeDescription = agentInfo?.description?.trim() || selectedAgentData?.description?.trim() || "請從左側選擇代理，開始企業知識、資料與流程協作。";

  return (
    <div className="hub-shell">
      <button
        type="button"
        className="hub-mobile-toggle"
        onClick={() => setIsMobileNavOpen((open) => !open)}
        aria-label={isMobileNavOpen ? "關閉代理目錄" : "開啟代理目錄"}
      >
        {isMobileNavOpen ? <X size={18} /> : <Menu size={18} />}
      </button>

      <AgentSidebar
        agents={agents}
        router={router}
        groups={groups}
        selectedAgent={selectedAgent}
        loading={loadingAgents}
        error={agentLoadError}
        searchQ={searchQ}
        onSearchChange={setSearchQ}
        onPickAgent={pickAgent}
        compact={isDesktopCompact}
        mobileOpen={isMobileNavOpen}
      />

      <button type="button" aria-hidden="true" className={`hub-scrim ${isMobileNavOpen ? "is-visible" : ""}`} onClick={() => setIsMobileNavOpen(false)} />

      <main className="hub-main">
        <MainHeader
          selectedAgent={activeTitle}
          description={activeDescription}
          loading={loadingInfo}
          error={agentInfoError}
          visibleAgentCount={filteredAgents.length}
          fileCount={runtimeFiles.length}
          promptCount={starterPrompts.length}
          status={selectedAgentData?.status}
          compact={isDesktopCompact}
          onToggleSidebar={() => setIsDesktopCompact((compact) => !compact)}
        />

        <section className="hub-workspace" aria-label="代理工作區">
          <ConversationPanel
            selectedAgent={selectedAgent}
            agentInfo={agentInfo}
            runtimeUrl={runtimeUrl}
            loadingAgents={loadingAgents}
            loadingInfo={loadingInfo}
            agentError={agentLoadError || agentInfoError}
          />

          <RunContextPanel
            files={runtimeFiles}
            prompts={starterPrompts}
            fileCount={runtimeFiles.length}
            uploadStatus={uploadStatus}
            uploadState={uploadState}
            uploading={isUploadingFiles}
            dragActive={isFileDragActive}
            open={isRunContextOpen}
            onToggleOpen={() => setIsRunContextOpen((open) => !open)}
            onUpload={uploadFiles}
            onRemoveFile={removeUploadedFile}
            onClearFiles={clearUploadedFiles}
            onDragActiveChange={setIsFileDragActive}
          />
        </section>
      </main>
    </div>
  );
}

function AgentSidebar({
  agents, router, groups, selectedAgent, loading, error, searchQ, onSearchChange, onPickAgent, compact, mobileOpen,
}: {
  agents: FoundryAgent[];
  router: FoundryAgent | null;
  groups: AgentGroup[];
  selectedAgent: string;
  loading: boolean;
  error: string;
  searchQ: string;
  onSearchChange: (value: string) => void;
  onPickAgent: (name: string) => void;
  compact: boolean;
  mobileOpen: boolean;
}) {
  return (
    <aside className={`hub-sidebar ${compact ? "is-compact" : ""} ${mobileOpen ? "is-mobile-open" : ""}`}>
      <div className="hub-brand">
        <div className="hub-brand-mark"><Network size={16} /></div>
        <div className="hub-brand-copy">
          <p className="hub-brand-title">WITS Agent Atelier</p>
          <p className="hub-brand-sub">Agent Catalog</p>
        </div>
      </div>

      <div className="catalog-heading">
        <span>代理目錄</span>
        <StatusBadge label={`${agents.length} agents`} variant="neutral" />
      </div>

      <label className="hub-search">
        <Search size={14} />
        <input value={searchQ} onChange={(event) => onSearchChange(event.target.value)} placeholder="搜尋代理、能力或領域" aria-label="搜尋代理" />
      </label>

      <div className="hub-agent-scroll">
        {loading ? (
          <div className="agent-skeleton-grid" aria-label="代理載入中">
            {[1, 2, 3, 4, 5].map((id) => <div key={id} className="agent-skeleton" />)}
          </div>
        ) : error ? (
          <EmptyState icon={AlertCircle} title="代理清單無法載入" description={error} compact />
        ) : (
          <>
            {router && (
              <section className="agent-section">
                <p className="agent-section-label">Current Routing</p>
                <AgentListItem agent={router} isActive={router.name === selectedAgent} onClick={() => onPickAgent(router.name)} isRouter compact={compact} />
              </section>
            )}

            {groups.map((group) => {
              const GroupIcon = group.icon;
              return (
                <section key={group.category} className="agent-section">
                  <p className="agent-section-label with-icon"><GroupIcon size={12} /><span>{group.category}</span></p>
                  {group.agents.map((agent) => (
                    <AgentListItem key={agent.name} agent={agent} isActive={agent.name === selectedAgent} onClick={() => onPickAgent(agent.name)} isRouter={false} compact={compact} />
                  ))}
                </section>
              );
            })}

            {!router && groups.length === 0 && <EmptyState icon={Bot} title="沒有符合的代理" description="請調整搜尋條件。" compact />}
          </>
        )}
      </div>

      <div className="hub-side-footer">
        <p>Workspace</p>
        <strong>Enterprise AI Operations</strong>
      </div>
    </aside>
  );
}

function AgentListItem({ agent, isActive, onClick, isRouter, compact }: {
  agent: FoundryAgent;
  isActive: boolean;
  onClick: () => void;
  isRouter: boolean;
  compact: boolean;
}) {
  const visual = getAgentVisual(agent, isRouter);
  const Icon = visual.icon;
  const tags = getCapabilityTags(agent, isRouter);
  const category = getAgentCategory(agent, isRouter);

  return (
    <button type="button" className={`agent-button ${isActive ? "is-active" : ""}`} onClick={onClick}>
      <span className={`agent-icon ${visual.tone}`}><Icon size={15} /></span>
      <span className="agent-copy">
        <span className="agent-title-row">
          <span className="agent-name">{agent.name}</span>
          {!compact && <StatusBadge label={getStatusLabel(agent.status)} variant={getStatusVariant(agent.status)} />}
        </span>
        {!compact && (
          <>
            <span className="agent-description">{category}</span>
            <span className="agent-tags">{tags.map((tag) => <span key={tag} className="agent-tag">{tag}</span>)}</span>
          </>
        )}
      </span>
    </button>
  );
}

function MainHeader({ selectedAgent, description, loading, error, visibleAgentCount, fileCount, promptCount, status, compact, onToggleSidebar }: {
  selectedAgent: string;
  description: string;
  loading: boolean;
  error: string;
  visibleAgentCount: number;
  fileCount: number;
  promptCount: number;
  status?: string;
  compact: boolean;
  onToggleSidebar: () => void;
}) {
  return (
    <header className="hub-header card-rise">
      <div className="hub-header-left">
        <button type="button" className="hub-desktop-toggle" onClick={onToggleSidebar} aria-label={compact ? "展開代理目錄" : "收合代理目錄"}>
          {compact ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
        <div className="hub-heading-copy">
          <p className="hub-kicker">Current Agent</p>
          {loading ? <p className="hub-headline-skeleton" /> : <h1 className="hub-headline">{selectedAgent || "尚未選擇代理"}</h1>}
          <p className={`hub-subline ${error ? "is-error" : ""}`}>{error || description}</p>
        </div>
      </div>

      <div className="hub-header-meta" aria-label="目前代理狀態">
        <span className="meta-pill"><Users size={13} />{visibleAgentCount} 可見代理</span>
        <span className="meta-pill"><Paperclip size={13} />{fileCount} 附件</span>
        <span className="meta-pill"><Sparkles size={13} />{promptCount} 提示</span>
        <StatusBadge label={getStatusLabel(status)} variant={getStatusVariant(status)} />
      </div>
    </header>
  );
}

function RunContextPanel({
  files, prompts, fileCount, uploadStatus, uploadState, uploading, dragActive, open, onToggleOpen, onUpload, onRemoveFile, onClearFiles, onDragActiveChange,
}: {
  files: RuntimeFile[];
  prompts: string[];
  fileCount: number;
  uploadStatus: string;
  uploadState: UploadState;
  uploading: boolean;
  dragActive: boolean;
  open: boolean;
  onToggleOpen: () => void;
  onUpload: (files: FileList | null) => void;
  onRemoveFile: (filename: string) => void;
  onClearFiles: () => void;
  onDragActiveChange: (active: boolean) => void;
}) {
  return (
    <aside className={`run-context card-rise ${open ? "is-open" : "is-collapsed"}`} aria-label="執行上下文">
      <div className="run-context-head">
        <div>
          <p className="section-kicker">Run Context</p>
          <h2>執行上下文</h2>
          <p>{fileCount} 個附件會套用到下一次代理執行。</p>
        </div>
        <button type="button" className="context-toggle" onClick={onToggleOpen} aria-expanded={open}>
          {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      <div className="run-context-body">
        <div
          className={`file-dropzone ${dragActive ? "is-drag-active" : ""} ${uploading ? "is-uploading" : ""}`}
          onDragOver={(event) => { event.preventDefault(); onDragActiveChange(true); }}
          onDragLeave={() => onDragActiveChange(false)}
          onDrop={(event) => { event.preventDefault(); onDragActiveChange(false); onUpload(event.dataTransfer.files); }}
        >
          <div className="file-panel-head">
            <div className="file-title-stack">
              <p className="file-panel-title">執行檔案</p>
              <p className="file-panel-subtitle">CSV、Excel、JSON、PDF、文字檔</p>
            </div>
            <div className="file-actions">
              {fileCount > 0 && (
                <button type="button" className="file-clear-btn" onClick={onClearFiles} disabled={uploading}>
                  <Trash2 size={13} />清除
                </button>
              )}
              <label className={`file-upload-btn ${uploading ? "is-disabled" : ""}`}>
                <input
                  type="file"
                  multiple
                  accept=".csv,.xlsx,.xls,.json,.txt,.pdf,.parquet,.zip,.md,.log"
                  disabled={uploading}
                  onChange={(event) => { onUpload(event.target.files); event.currentTarget.value = ""; }}
                />
                {uploading ? <Loader2 size={14} className="spin" /> : <FileUp size={14} />}
                {uploading ? "上傳中" : "附加"}
              </label>
            </div>
          </div>

          <UploadStatus state={uploadState} message={uploadStatus} uploading={uploading} />
          <RuntimeFileList files={files} onRemoveFile={onRemoveFile} />
        </div>

        <SuggestedPromptChips prompts={prompts} />
      </div>
    </aside>
  );
}

function UploadStatus({ state, message, uploading }: { state: UploadState; message: string; uploading: boolean }) {
  if (!message && state === "idle") return null;
  const Icon = state === "error" ? AlertCircle : state === "success" ? CheckCircle2 : Loader2;
  return (
    <div className={`upload-status is-${state}`} role={state === "error" ? "alert" : "status"}>
      <Icon size={14} className={uploading ? "spin" : ""} />
      <span>{message || "準備上傳檔案。"}</span>
    </div>
  );
}

function RuntimeFileList({ files, onRemoveFile }: { files: RuntimeFile[]; onRemoveFile: (filename: string) => void }) {
  if (files.length === 0) {
    return <EmptyState icon={Paperclip} title="尚未附加檔案" description="拖曳檔案到這裡，或使用附加按鈕上傳到下一次執行。" compact />;
  }

  return (
    <div className="runtime-file-list">
      {files.map((file) => {
        const Icon = file.Icon;
        return (
          <article key={`${file.filename}-${file.fileId}`} className="runtime-file-card">
            <span className="runtime-file-icon"><Icon size={16} /></span>
            <div className="runtime-file-copy">
              <strong title={file.filename}>{file.filename}</strong>
              <span>{file.typeLabel}</span>
            </div>
            <button type="button" className="runtime-file-remove" onClick={() => onRemoveFile(file.filename)} aria-label={`移除 ${file.filename}`}>
              <X size={14} />
            </button>
          </article>
        );
      })}
    </div>
  );
}

function SuggestedPromptChips({ prompts }: { prompts: string[] }) {
  return (
    <section className="suggested-prompts" aria-label="建議提示">
      <div className="context-section-title"><MessageSquare size={14} /><span>建議提示</span></div>
      <div className="prompt-chip-row">
        {prompts.slice(0, 4).map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="prompt-chip"
            onClick={() => { void navigator.clipboard?.writeText(prompt); }}
            title="點擊複製提示"
          >
            {prompt}
          </button>
        ))}
      </div>
    </section>
  );
}

function ConversationPanel({ selectedAgent, agentInfo, runtimeUrl, loadingAgents, loadingInfo, agentError }: {
  selectedAgent: string;
  agentInfo: AgentInfo | null;
  runtimeUrl: string;
  loadingAgents: boolean;
  loadingInfo: boolean;
  agentError: string;
}) {
  if (loadingAgents) {
    return <section className="conversation-panel card-rise"><EmptyState icon={Loader2} title="正在載入代理" description="正在向後端探索可用 Foundry 代理。" /></section>;
  }

  if (!selectedAgent) {
    return (
      <section className="conversation-panel card-rise">
        <EmptyState icon={Network} title="尚未選擇代理" description={agentError || "請從代理目錄選擇一個代理。"} />
      </section>
    );
  }

  return (
    <section className="conversation-panel card-rise" aria-label="主要對話工作區">
      <CopilotKit key={selectedAgent} runtimeUrl={runtimeUrl} showDevConsole={false} agent={selectedAgent}>
        <ChatArea selectedAgent={selectedAgent} agentInfo={agentInfo} loadingInfo={loadingInfo} />
      </CopilotKit>
      <GeneratedFiles files={[]} />
    </section>
  );
}

function ChatArea({ selectedAgent, agentInfo, loadingInfo }: { selectedAgent: string; agentInfo: AgentInfo | null; loadingInfo: boolean }) {
  const { state } = useCoAgent<PowerBIState>({ name: selectedAgent, initialState: INITIAL_POWERBI_STATE });
  const displayName = agentInfo?.welcomeMessage?.trim() || agentInfo?.name || selectedAgent;
  const assistantIdentityStyle = {
    "--assistant-name": JSON.stringify(displayName),
    "--agent-avatar-url": "url('/favicon.ico')",
  } as React.CSSProperties;

  return (
    <div className="chat-surface" style={assistantIdentityStyle}>
      <StateSnapshot state={state ?? INITIAL_POWERBI_STATE} loading={loadingInfo} />
      <CopilotChat labels={{ title: displayName, initial: "", placeholder: `輸入給 ${displayName} 的訊息...` }} />
      <div className="chat-footnote">企業決策或外部輸出前，請再次驗證代理回覆與產出檔案。</div>
    </div>
  );
}

function StateSnapshot({ state, loading }: { state: PowerBIState; loading: boolean }) {
  const points = Array.isArray(state?.data_points) ? state.data_points : [];
  const latestPoint = points.length > 0 ? points[points.length - 1] : undefined;
  const dashboardContext = state?.dashboard_context || "";
  const currentInsights = state?.current_insights || "";
  const hasState = Boolean(dashboardContext || currentInsights || points.length > 0);

  if (!hasState) {
    return (
      <section className="state-snapshot is-empty" aria-label="AG-UI shared state">
        <span className="snapshot-dot" />
        <span>{loading ? "正在讀取代理資訊" : "尚無 AG-UI 狀態更新"}</span>
      </section>
    );
  }

  return (
    <section className="state-snapshot" aria-label="AG-UI shared state">
      <div className="snapshot-row"><span className="snapshot-label">Dashboard</span><span className="snapshot-value">{dashboardContext || "未設定"}</span></div>
      <div className="snapshot-row"><span className="snapshot-label">Insights</span><span className="snapshot-value">{currentInsights || "尚無洞察"}</span></div>
      <div className="snapshot-row"><span className="snapshot-label">Data Points</span><span className="snapshot-value">{points.length}</span></div>
      {latestPoint && <div className="snapshot-row"><span className="snapshot-label">Latest Metric</span><span className="snapshot-value">{latestPoint.metric_name}: {latestPoint.value}</span></div>}
    </section>
  );
}

function GeneratedFiles({ files }: { files: GeneratedFileItem[] }) {
  if (files.length === 0) return null;
  return (
    <section className="generated-files" aria-label="產出檔案">
      <div className="context-section-title"><FileUp size={14} /><span>Generated Files</span></div>
      <div className="generated-file-grid">
        {files.map((file) => (
          <article key={file.href} className="generated-file-card">
            <div>
              <strong>{file.filename}</strong>
              <span>{file.type || "檔案"}{file.size ? ` · ${file.size}` : ""}</span>
              {file.createdAt && <span>{file.createdAt}</span>}
            </div>
            <a className="primary-action" href={file.href} download>下載</a>
          </article>
        ))}
      </div>
    </section>
  );
}

function EmptyState({ icon: Icon, title, description, compact = false }: {
  icon: LucideIcon;
  title: string;
  description: string;
  compact?: boolean;
}) {
  return (
    <div className={`empty-state ${compact ? "is-compact" : ""}`}>
      <div className="empty-icon"><Icon size={compact ? 16 : 22} className={Icon === Loader2 ? "spin" : ""} /></div>
      <div>
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
    </div>
  );
}

function ChatMessage({ role, children }: { role: "user" | "assistant"; children: React.ReactNode }) {
  return <div className={`chat-message is-${role}`}>{children}</div>;
}

function StatusBadge({ label, variant }: { label: string; variant: StatusVariant }) {
  return <span className={`status-badge is-${variant}`}>{label}</span>;
}

function useSuppressConsoleNoise() {
  useEffect(() => {
    const originalError = console.error;
    console.error = (...args: unknown[]) => {
      const message = typeof args[0] === "string" ? args[0] : "";
      if (message.includes("Cannot send 'TOOL_CALL_END'") && message.includes("TOOL_CALL_START")) return;
      originalError(...args);
    };

    return () => {
      console.error = originalError;
    };
  }, []);
}
