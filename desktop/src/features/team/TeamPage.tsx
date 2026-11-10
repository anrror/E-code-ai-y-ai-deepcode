import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  Eye,
  Layers,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import type {
  MethodResults,
  Project,
  TeamEntityView,
  TeamKnowledgeView,
} from "../../generated/app-server";
import type { ClientRuntime } from "../../rpc/contracts";

import styles from "./TeamPage.module.css";

const KINDS = [
  "org",
  "line",
  "product",
  "project",
  "batch",
  "version",
  "task",
] as const;
type Kind = (typeof KINDS)[number];

const PARENT_KINDS: Record<string, string[]> = {
  org: [],
  line: ["org"],
  product: ["org", "line"],
  project: ["line"],
  batch: ["project"],
  version: ["batch"],
  task: ["version"],
};

const KIND_LABELS: Record<Kind, string> = {
  org: "组织",
  line: "业务线",
  product: "产品",
  project: "项目",
  batch: "批次",
  version: "版本",
  task: "任务",
};

const KNOWLEDGE_TYPES = [
  "redline",
  "decision",
  "convention",
  "domain",
  "design",
  "lesson",
  "external",
] as const;

const TYPE_LABELS: Record<string, string> = {
  redline: "红线",
  decision: "决策",
  convention: "约定",
  domain: "领域",
  design: "设计",
  lesson: "教训",
  external: "外部",
  compliance: "合规",
  license: "许可证",
  toolchain: "工具链",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  reviewed: "已评审",
  deprecated: "已废弃",
};

const ORG_SECTIONS = [
  ["redline", "安全红线"],
  ["compliance", "数据合规"],
  ["license", "许可证策略"],
  ["toolchain", "统一工具链"],
] as const;

const ORG_POLICY_TYPES: string[] = ORG_SECTIONS.map(([type]) => type);

const CONFIDENCE_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

type TreeResult = MethodResults["team/tree"];
type ReadResult = MethodResults["team/read"];
type PreviewResult = MethodResults["team/preview"];

interface EntryForm {
  type: string;
  statement: string;
  rationale: string;
  evidence: string;
  confidence: string;
  owner: string;
}

interface CreateForm {
  kind: Kind;
  slug: string;
  name: string;
  parent: string;
  external: boolean;
}

interface TeamPageProps {
  runtime: ClientRuntime;
  project: Project | null;
}

function emptyEntryForm(entry?: TeamKnowledgeView): EntryForm {
  return {
    type: entry?.type ?? "decision",
    statement: entry?.statement ?? "",
    rationale: entry?.rationale ?? "",
    evidence: entry?.evidence ?? "",
    confidence: entry?.confidence ?? "medium",
    owner: entry?.owner ?? "",
  };
}

function kindIndex(kind: string): number {
  return KINDS.indexOf(kind as Kind);
}

function sectionIndex(type: string): number {
  const index = ORG_POLICY_TYPES.indexOf(type);
  return index === -1 ? ORG_POLICY_TYPES.length : index;
}

export function TeamPage({ runtime, project }: TeamPageProps) {
  const { t } = useTranslation();
  const [tree, setTree] = useState<TreeResult | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReadResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [queueMode, setQueueMode] = useState(false);
  const [queue, setQueue] = useState<
    { entity: TeamEntityView; entry: TeamKnowledgeView }[]
  >([]);
  const [queueLoading, setQueueLoading] = useState(false);

  const [statusFilter, setStatusFilter] = useState<
    "all" | "draft" | "reviewed"
  >("all");
  const [query, setQuery] = useState("");

  const [createForm, setCreateForm] = useState<CreateForm | null>(null);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [entryDraft, setEntryDraft] = useState<{
    entry?: TeamKnowledgeView;
    form: EntryForm;
  } | null>(null);
  const [entryBusy, setEntryBusy] = useState(false);
  const [entryError, setEntryError] = useState<string | null>(null);

  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [orgUnlocked, setOrgUnlocked] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editParent, setEditParent] = useState("");
  const [editExternal, setEditExternal] = useState(false);

  const loadTree = useCallback(async () => {
    if (!project) {
      setTree(null);
      return;
    }
    setBusy(true);
    try {
      const result = await runtime.request("team/tree", {
        projectId: project.id,
      });
      setTree(result);
      setSelectedId((current) =>
        result.entities.some((entity) => entity.id === current)
          ? current
          : (result.bind ?? result.entities[0]?.id ?? null),
      );
    } catch (caught) {
      setTree(null);
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  }, [project, runtime]);

  const loadDetail = useCallback(
    async (entityId: string) => {
      if (!project) return;
      try {
        const result = await runtime.request("team/read", {
          projectId: project.id,
          entityId,
        });
        setDetail(result);
      } catch (caught) {
        setDetail(null);
        setError(String(caught));
      }
    },
    [project, runtime],
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- page load
    void loadTree();
  }, [loadTree]);

  useEffect(() => {
    if (!selectedId) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- selection fetch
    void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  const entityForKind = useCallback(
    (kind: Kind): TeamEntityView | undefined => {
      const candidates = (tree?.entities ?? []).filter(
        (entity) => entity.kind === kind,
      );
      if (candidates.length === 0) return undefined;
      const inChain = candidates.find((entity) =>
        tree?.inChain.includes(entity.id),
      );
      return inChain ?? candidates[0];
    },
    [tree],
  );

  const draftTotal = useMemo(
    () =>
      (tree?.entities ?? []).reduce(
        (total, entity) => total + entity.draftCount,
        0,
      ),
    [tree],
  );

  const parentOptions = useMemo(() => {
    if (!createForm) return [];
    const limit = kindIndex(createForm.kind);
    return (tree?.entities ?? []).filter(
      (entity) => kindIndex(entity.kind) >= 0 && kindIndex(entity.kind) < limit,
    );
  }, [createForm, tree]);

  const loadQueue = useCallback(async () => {
    if (!project || !tree) return;
    setQueueLoading(true);
    try {
      const details = await Promise.all(
        tree.entities.map((entity) =>
          runtime.request("team/read", {
            projectId: project.id,
            entityId: entity.id,
          }),
        ),
      );
      const drafts = details.flatMap((entry, index) =>
        entry.knowledge
          .filter((item) => item.status === "draft")
          .map((item) => ({ entity: tree.entities[index], entry: item })),
      );
      setQueue(drafts);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setQueueLoading(false);
    }
  }, [project, runtime, tree]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- queue is fetched when opened
    if (queueMode) void loadQueue();
  }, [queueMode, loadQueue]);

  const openCreate = (kind: Kind) => {
    if (kind === "org") {
      setCreateError(null);
      setCreateForm({
        kind,
        slug: "default",
        name: "组织",
        parent: "",
        external: false,
      });
      return;
    }
    const parent =
      (tree?.inChain ?? [])
        .map((id) => tree?.entities.find((entity) => entity.id === id))
        .filter((entity): entity is TeamEntityView => Boolean(entity))
        .filter((entity) => kindIndex(entity.kind) < kindIndex(kind))
        .pop()?.id ?? "";
    setCreateError(null);
    setCreateForm({ kind, slug: "", name: "", parent, external: false });
  };

  const submitCreate = async () => {
    if (!project || !createForm) return;
    const name = createForm.name.trim();
    const slug =
      createForm.kind === "org"
        ? "default"
        : (name || createForm.slug.trim()).replace(/\s+/g, "-");
    if (!slug) {
      setCreateError(t("team.nameRequired", "请填写名称"));
      return;
    }
    setCreateBusy(true);
    setCreateError(null);
    try {
      await runtime.request("team/create", {
        projectId: project.id,
        kind: createForm.kind,
        slug,
        name: name || undefined,
        parent: createForm.parent || undefined,
        external: createForm.external,
        shared: createForm.kind === "org",
      });
      setCreateForm(null);
      if (createForm.kind === "org") setOrgUnlocked(true);
      await loadTree();
      setSelectedId(`${createForm.kind}://${slug}`);
    } catch (caught) {
      setCreateError(String(caught));
    } finally {
      setCreateBusy(false);
    }
  };

  const openNewEntry = (type: string = "decision") => {
    setEntryError(null);
    setEntryDraft({ form: { ...emptyEntryForm(), type } });
  };

  const openEntry = (entry: TeamKnowledgeView) => {
    setEntryError(null);
    setEntryDraft({ entry, form: emptyEntryForm(entry) });
  };

  const submitEntry = async (review: boolean) => {
    if (!project || !selectedId || !entryDraft) return;
    const statement = entryDraft.form.statement.trim();
    if (!statement) {
      setEntryError(t("team.statementRequired", "请填写陈述内容"));
      return;
    }
    setEntryBusy(true);
    setEntryError(null);
    try {
      const shared = detail?.entity.kind === "org";
      const params = {
        projectId: project.id,
        type: entryDraft.form.type,
        statement,
        rationale: entryDraft.form.rationale.trim() || undefined,
        evidence: entryDraft.form.evidence.trim() || undefined,
        confidence: entryDraft.form.confidence,
        owner: entryDraft.form.owner.trim() || undefined,
      };
      let knowledgeId: string;
      if (entryDraft.entry) {
        const updated = await runtime.request("team/knowledge/update", {
          ...params,
          knowledgeId: entryDraft.entry.id,
          shared,
        });
        knowledgeId = updated.entry.id;
      } else {
        const added = await runtime.request("team/knowledge/add", {
          ...params,
          entityId: selectedId,
          shared,
        });
        knowledgeId = added.entry.id;
      }
      if (review) {
        await runtime.request("team/knowledge/promote", {
          projectId: project.id,
          knowledgeId,
          owner: entryDraft.form.owner.trim() || undefined,
          shared,
        });
      }
      setEntryDraft(null);
      await loadDetail(selectedId);
      await loadTree();
    } catch (caught) {
      setEntryError(String(caught));
    } finally {
      setEntryBusy(false);
    }
  };

  const promote = async (knowledgeId: string) => {
    if (!project || !selectedId) return;
    setBusy(true);
    try {
      await runtime.request("team/knowledge/promote", {
        projectId: project.id,
        knowledgeId,
        shared: detail?.entity.kind === "org",
      });
      await loadDetail(selectedId);
      await loadTree();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const removeEntry = async (knowledgeId: string) => {
    if (!project || !selectedId) return;
    setBusy(true);
    try {
      await runtime.request("team/knowledge/remove", {
        projectId: project.id,
        knowledgeId,
        shared: detail?.entity.kind === "org",
      });
      setConfirmDelete(null);
      await loadDetail(selectedId);
      await loadTree();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const policyAdd = async (type: string, statement: string) => {
    if (!project || !selectedId || !statement.trim()) return;
    setBusy(true);
    try {
      await runtime.request("team/knowledge/add", {
        projectId: project.id,
        entityId: selectedId,
        type,
        statement: statement.trim(),
        shared: true,
      });
      await loadDetail(selectedId);
      await loadTree();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const policySave = async (entry: TeamKnowledgeView, statement: string) => {
    if (!project || !statement.trim()) return;
    setBusy(true);
    try {
      await runtime.request("team/knowledge/update", {
        projectId: project.id,
        knowledgeId: entry.id,
        statement: statement.trim(),
        shared: true,
      });
      if (selectedId) await loadDetail(selectedId);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const saveLayer = async () => {
    if (!project || !selectedId) return;
    setBusy(true);
    try {
      await runtime.request("team/entity/update", {
        projectId: project.id,
        entityId: selectedId,
        name: editName.trim() || undefined,
        parent: editParent,
        external: editExternal,
        shared: detail?.entity.kind === "org",
      });
      setEditing(false);
      await loadTree();
      await loadDetail(selectedId);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const showPreview = async () => {
    if (!project) return;
    setPreviewBusy(true);
    try {
      const result = await runtime.request("team/preview", {
        projectId: project.id,
        entityId: tree?.bind ?? selectedId ?? undefined,
        includeExternal: true,
      });
      setPreview(result);
      setCopied(false);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setPreviewBusy(false);
    }
  };

  const copyPreview = async () => {
    if (!preview) return;
    try {
      await navigator.clipboard.writeText(preview.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  if (!project) {
    return (
      <section className={styles.page}>
        <p className={styles.placeholder}>
          {t("team.openProject", "打开一个项目以查看团队上下文。")}
        </p>
      </section>
    );
  }

  const activeDetail =
    detail && detail.entity.id === selectedId ? detail : null;
  const knowledge = activeDetail?.knowledge ?? [];
  const visibleKnowledge = knowledge.filter((entry) => {
    if (statusFilter !== "all" && entry.status !== statusFilter) return false;
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return [entry.statement, entry.owner, entry.type, entry.id]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
  if (detail?.entity.kind === "org") {
    visibleKnowledge.sort(
      (left, right) => sectionIndex(left.type) - sectionIndex(right.type),
    );
  }

  const selectedEntity =
    activeDetail?.entity ??
    tree?.entities.find((entity) => entity.id === selectedId) ??
    null;

  const createIdPreview = createForm
    ? `${createForm.kind}://${createForm.slug.trim() || "…"}`
    : "";

  return (
    <section className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <p className={styles.eyebrow}>{t("team.eyebrow", "团队上下文")}</p>
          <h2>{project.displayName}</h2>
          <p className={styles.binding}>
            {tree?.bind ? (
              <>
                {t("team.boundTask", "当前任务")} <code>{tree.bind}</code>
              </>
            ) : (
              t("team.unbound", "当前工作区尚未绑定任务；任务层不参与装配。")
            )}
          </p>
        </div>
        <div className={styles.headerActions}>
          <button
            type="button"
            className={styles.primary}
            disabled={previewBusy}
            onClick={() => void showPreview()}
          >
            <Eye size={14} />
            {t("team.preview", "预览装配")}
          </button>
          <button
            type="button"
            className={styles.ghost}
            onClick={() => openCreate("project")}
          >
            <Plus size={14} />
            {t("team.newLayer", "新建层")}
          </button>
          <button
            type="button"
            className={styles.iconButton}
            disabled={busy}
            onClick={() => void loadTree()}
            aria-label={t("team.refresh", "刷新")}
          >
            <RefreshCw size={15} />
          </button>
        </div>
      </header>

      <ol className={styles.chain} aria-label={t("team.chain", "上下文链")}>
        {KINDS.map((kind) => {
          const entity = entityForKind(kind);
          const linked = entity
            ? (tree?.inChain.includes(entity.id) ?? false)
            : false;
          return (
            <li key={kind} className={styles.chainItem}>
              <button
                type="button"
                className={styles.chainNode}
                data-state={!entity ? "missing" : linked ? "linked" : "idle"}
                data-active={entity?.id === selectedId}
                onClick={() =>
                  entity ? setSelectedId(entity.id) : openCreate(kind)
                }
                title={entity?.id ?? `${kind}://`}
              >
                <span className={styles.chainKind}>{KIND_LABELS[kind]}</span>
                <span className={styles.chainName}>
                  {entity ? entity.name : t("team.notConfigured", "未配置")}
                </span>
                {entity ? (
                  <span className={styles.chainMeta}>
                    {entity.knowledgeCount - entity.draftCount}
                    {entity.draftCount > 0 ? ` · ${entity.draftCount} 草稿` : ""}
                    {entity.external ? " · 外部" : ""}
                    {entity.archived ? " · 历史" : ""}
                  </span>
                ) : (
                  <span className={styles.chainMeta}>
                    <Plus size={11} /> {t("team.add", "添加")}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ol>

      {error ? (
        <p className={styles.banner} role="alert">
          {error}
          <button type="button" onClick={() => setError(null)} aria-label="关闭">
            <X size={13} />
          </button>
        </p>
      ) : null}

      <div className={styles.body}>
        <aside className={styles.sidebar}>
          <div className={styles.switcher}>
            <button
              type="button"
              data-active={!queueMode}
              onClick={() => setQueueMode(false)}
            >
              {t("team.layers", "层")}
            </button>
            <button
              type="button"
              data-active={queueMode}
              onClick={() => setQueueMode(true)}
            >
              {t("team.reviewQueue", "待评审")}
              {draftTotal > 0 ? (
                <span className={styles.count}>{draftTotal}</span>
              ) : null}
            </button>
          </div>

          {queueMode ? (
            <div className={styles.queue}>
              {queueLoading ? (
                <p className={styles.muted}>{t("team.loading", "加载中…")}</p>
              ) : queue.length === 0 ? (
                <p className={styles.muted}>
                  {t("team.queueEmpty", "没有待评审的条目。")}
                </p>
              ) : (
                queue.map((item) => (
                  <button
                    key={item.entry.id}
                    type="button"
                    className={styles.queueItem}
                    onClick={() => {
                      setSelectedId(item.entity.id);
                      openEntry(item.entry);
                    }}
                  >
                    <span>{item.entry.statement}</span>
                    <em>
                      {item.entity.name} ·{" "}
                      {TYPE_LABELS[item.entry.type] ?? item.entry.type}
                    </em>
                  </button>
                ))
              )}
            </div>
          ) : (
            <ul className={styles.layers}>
              {KINDS.map((kind) => {
                const entity = entityForKind(kind);
                return (
                  <li key={kind}>
                    <button
                      type="button"
                      className={styles.layerRow}
                      data-active={entity?.id === selectedId}
                      onClick={() =>
                        entity ? setSelectedId(entity.id) : openCreate(kind)
                      }
                    >
                      <span className={styles.layerKind}>
                        {KIND_LABELS[kind]}
                      </span>
                      <span className={styles.layerName}>
                        {entity ? entity.name : "＋ 添加"}
                      </span>
                      {entity ? (
                        <span className={styles.layerCounts}>
                          {entity.knowledgeCount}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <main className={styles.detail}>
          {selectedEntity ? (
            <>
              <div className={styles.detailHead}>
                <div>
                  <h3>{selectedEntity.name}</h3>
                  <code className={styles.entityId}>{selectedEntity.id}</code>
                </div>
                <div className={styles.badges}>
                  <button
                    type="button"
                    className={styles.ghost}
                    disabled={selectedEntity.kind === "org" && !orgUnlocked}
                    onClick={() => {
                      setEditing(true);
                      setEditName(selectedEntity.name);
                      setEditParent(selectedEntity.parent ?? "");
                      setEditExternal(selectedEntity.external);
                    }}
                  >
                    编辑层信息
                  </button>
                  {selectedEntity.external ? (
                    <span className={styles.badge}>
                      {t("team.external", "外部 · 未验证")}
                    </span>
                  ) : null}
                  {selectedEntity.archived ? (
                    <span className={styles.badge}>
                      {t("team.historical", "历史")}
                    </span>
                  ) : null}
                  {selectedEntity.kind === "org" ? null : (
                    <button
                      type="button"
                      className={styles.primary}
                      onClick={() => openNewEntry("decision")}
                    >
                      <Plus size={14} />
                      {t("team.addKnowledge", "添加知识")}
                    </button>
                  )}
                </div>
              </div>

              {selectedEntity.kind === "org" ? (
                <div className={styles.orgBanner}>
                  <div>
                    <strong>组织策略</strong>
                    <span>
                      对本机所有项目生效；红线条目不可被下层覆盖。
                    </span>
                  </div>
                  {orgUnlocked ? (
                    <span className={styles.bannerActions}>
                      <button
                        type="button"
                        className={styles.ghost}
                        onClick={() => setOrgUnlocked(false)}
                      >
                        锁定
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className={styles.ghost}
                      onClick={() => setOrgUnlocked(true)}
                    >
                      解锁编辑
                    </button>
                  )}
                </div>
              ) : null}

              {activeDetail ? (
                <>
                  {activeDetail.instructions ? (
                    <details className={styles.disclosure}>
                      <summary>
                        <ChevronDown size={13} />
                        AGENTS.md · {activeDetail.instructions.length} 字符
                      </summary>
                      <pre>{activeDetail.instructions}</pre>
                    </details>
                  ) : (
                    <p className={styles.muted}>
                      {t("team.noInstructions", "该层没有 AGENTS.md。")}
                    </p>
                  )}

                  {selectedEntity.kind === "org" ? (
                    <OrgPolicyEditor
                      knowledge={knowledge}
                      unlocked={orgUnlocked}
                      busy={busy}
                      onAdd={(type, statement) => void policyAdd(type, statement)}
                      onSave={(entry, statement) =>
                        void policySave(entry, statement)
                      }
                      onPromote={(id) => void promote(id)}
                      onRemove={(id) => void removeEntry(id)}
                    />
                  ) : (
                    <>
                  <div className={styles.toolbar}>
                    <div className={styles.segmented}>
                      {(
                        [
                          ["all", "全部"],
                          ["draft", "草稿"],
                          ["reviewed", "已评审"],
                        ] as const
                      ).map(([value, label]) => (
                        <button
                          key={value}
                          type="button"
                          data-active={statusFilter === value}
                          onClick={() => setStatusFilter(value)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <input
                      type="search"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder={t("team.search", "搜索陈述 / 所有者 / 类型")}
                      aria-label={t("team.search", "搜索陈述 / 所有者 / 类型")}
                    />
                  </div>

                  {visibleKnowledge.length === 0 ? (
                    <p className={styles.muted}>
                      {knowledge.length === 0
                        ? t("team.noKnowledge", "该层还没有知识条目。")
                        : t("team.noMatch", "没有符合筛选条件的条目。")}
                    </p>
                  ) : (
                    <table className={styles.table}>
                      <thead>
                        <tr>
                          <th>{t("team.type", "类型")}</th>
                          <th>{t("team.statement", "陈述")}</th>
                          <th>{t("team.confidence", "置信度")}</th>
                          <th>{t("team.status", "状态")}</th>
                          <th>{t("team.owner", "所有者")}</th>
                          <th aria-label={t("team.actions", "操作")} />
                        </tr>
                      </thead>
                      <tbody>
                        {visibleKnowledge.map((entry, index) => {
                          const previous = visibleKnowledge[index - 1];
                          const showSection =
                            selectedEntity.kind === "org" &&
                            (!previous || previous.type !== entry.type);
                          return (
                          <Fragment key={entry.id}>
                          {showSection ? (
                            <tr className={styles.sectionRow}>
                              <td colSpan={6}>
                                {TYPE_LABELS[entry.type] ?? entry.type}
                              </td>
                            </tr>
                          ) : null}
                          <tr data-status={entry.status}>
                            <td>
                              <span className={styles.typeChip}>
                                {TYPE_LABELS[entry.type] ?? entry.type}
                              </span>
                            </td>
                            <td className={styles.statement}>
                              {entry.statement}
                            </td>
                            <td>
                              {CONFIDENCE_LABELS[entry.confidence] ??
                                entry.confidence}
                            </td>
                            <td>
                              <span
                                className={styles.statusChip}
                                data-status={entry.status}
                              >
                                {STATUS_LABELS[entry.status] ?? entry.status}
                              </span>
                            </td>
                            <td>{entry.owner || "—"}</td>
                            <td className={styles.rowActions}>
                              {entry.status === "draft" &&
                              !(selectedEntity.kind === "org" && !orgUnlocked) ? (
                                confirmDelete === entry.id ? (
                                  <>
                                    <button
                                      type="button"
                                      className={styles.danger}
                                      disabled={busy}
                                      onClick={() => void removeEntry(entry.id)}
                                    >
                                      {t("team.confirmDelete", "确认删除")}
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => setConfirmDelete(null)}
                                    >
                                      {t("team.cancel", "取消")}
                                    </button>
                                  </>
                                ) : (
                                  <>
                                    <button
                                      type="button"
                                      aria-label={t("team.edit", "编辑")}
                                      title={t("team.edit", "编辑")}
                                      onClick={() => openEntry(entry)}
                                    >
                                      <Pencil size={13} />
                                    </button>
                                    <button
                                      type="button"
                                      aria-label={t(
                                        "team.markReviewed",
                                        "标记已评审",
                                      )}
                                      title={t("team.markReviewed", "标记已评审")}
                                      onClick={() => void promote(entry.id)}
                                    >
                                      <Check size={13} />
                                    </button>
                                    <button
                                      type="button"
                                      aria-label={t("team.delete", "删除")}
                                      title={t("team.delete", "删除")}
                                      onClick={() => setConfirmDelete(entry.id)}
                                    >
                                      <Trash2 size={13} />
                                    </button>
                                  </>
                                )
                              ) : (
                                <span
                                  className={styles.locked}
                                  title={t(
                                    "team.lockedHint",
                                    "已评审条目不可改；如需变更请新增条目替代。",
                                  )}
                                >
                                  <Lock size={12} />
                                </span>
                              )}
                            </td>
                          </tr>
                          </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  )}

                    </>
                  )}

                  {editing ? (
                    <form
                      className={styles.layerForm}
                      onSubmit={(event) => {
                        event.preventDefault();
                        void saveLayer();
                      }}
                    >
                      <label>
                        <span>名称</span>
                        <input
                          autoFocus
                          value={editName}
                          onChange={(event) => setEditName(event.target.value)}
                        />
                      </label>
                      <label>
                        <span>上级</span>
                        <select
                          value={editParent}
                          onChange={(event) => setEditParent(event.target.value)}
                        >
                          <option value="">无</option>
                          {(tree?.entities ?? [])
                            .filter((entity) =>
                              (PARENT_KINDS[selectedEntity.kind] ?? []).includes(
                                entity.kind,
                              ),
                            )
                            .map((entity) => (
                              <option key={entity.id} value={entity.id}>
                                {KIND_LABELS[entity.kind as Kind] ?? entity.kind} ·{" "}
                                {entity.name}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label className={styles.checkbox}>
                        <input
                          type="checkbox"
                          checked={editExternal}
                          onChange={(event) =>
                            setEditExternal(event.target.checked)
                          }
                        />
                        <span>外部（未验证来源）</span>
                      </label>
                      <div className={styles.formActions}>
                        <button type="submit" disabled={busy}>
                          保存
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditing(false)}
                        >
                          取消
                        </button>
                      </div>
                    </form>
                  ) : (
                  <details className={styles.disclosure}>
                    <summary>
                      <ChevronDown size={13} />
                      {t("team.details", "层信息")}
                    </summary>
                    <dl className={styles.meta}>
                      <dt>{t("team.parent", "父层")}</dt>
                      <dd>{selectedEntity.parent ?? "—"}</dd>
                      <dt>{t("team.workspace", "工作区")}</dt>
                      <dd>{selectedEntity.workspace ?? "—"}</dd>
                      <dt>{t("team.references", "引用")}</dt>
                      <dd>{selectedEntity.references?.join("、") || "—"}</dd>
                      <dt>{t("team.designs", "设计")}</dt>
                      <dd>{selectedEntity.designs?.join("、") || "—"}</dd>
                      <dt>{t("team.codebases", "代码库")}</dt>
                      <dd>{selectedEntity.codebases?.join("、") || "—"}</dd>
                    </dl>
                  </details>
                  )}
                </>
              ) : (
                <p className={styles.muted}>{t("team.loading", "加载中…")}</p>
              )}
            </>
          ) : (
            <div className={styles.emptyState}>
              <Layers size={20} />
              <p>
                {t(
                  "team.emptyChain",
                  "还没有配置任何层。六层是可选的：未配置的层不贡献内容。从“项目”层开始即可。",
                )}
              </p>
              <button
                type="button"
                className={styles.primary}
                onClick={() => openCreate("project")}
              >
                <Plus size={14} />
                {t("team.startWithProject", "创建项目层")}
              </button>
            </div>
          )}
        </main>
      </div>

      {createForm ? (
        <Modal
          title={t("team.createLayer", "新建层")}
          onClose={() => setCreateForm(null)}
          footer={
            <>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => setCreateForm(null)}
                disabled={createBusy}
              >
                {t("team.cancel", "取消")}
              </button>
              <button
                type="button"
                className={styles.primary}
                onClick={() => void submitCreate()}
                disabled={createBusy}
              >
                {t("team.create", "创建")}
              </button>
            </>
          }
        >
          <div className={styles.formGrid}>
            <label>
              <span>{t("team.kind", "层级")}</span>
              <select
                value={createForm.kind}
                onChange={(event) =>
                  setCreateForm({
                    ...createForm,
                    kind: event.target.value as Kind,
                  })
                }
              >
                {KINDS.map((kind) => (
                  <option
                    key={kind}
                    value={kind}
                    disabled={
                      kind === "org" && Boolean(entityForKind("org"))
                    }
                  >
                    {KIND_LABELS[kind]}（{kind}）
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.wide}>
              <span>{t("team.displayName", "名称")}</span>
              <input
                autoFocus
                value={createForm.name}
                placeholder={t("team.nameHint", "例如：长安项目、自动化外包线")}
                onChange={(event) =>
                  setCreateForm({ ...createForm, name: event.target.value })
                }
              />
            </label>
            {createForm.kind === "org" ? null : (
            <label>
              <span>{t("team.parent", "父层")}</span>
              <select
                value={createForm.parent}
                onChange={(event) =>
                  setCreateForm({ ...createForm, parent: event.target.value })
                }
              >
                <option value="">{t("team.noParent", "无")}</option>
                {parentOptions.map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {KIND_LABELS[entity.kind as Kind] ?? entity.kind} ·{" "}
                    {entity.name}
                  </option>
                ))}
              </select>
            </label>
            )}
            {createForm.kind === "org" ? null : (
            <label className={styles.checkbox}>
              <input
                type="checkbox"
                checked={createForm.external}
                onChange={(event) =>
                  setCreateForm({
                    ...createForm,
                    external: event.target.checked,
                  })
                }
              />
              <span>
                {t(
                  "team.externalHint",
                  "外部：来自未验证来源，会明确标注给 agent",
                )}
              </span>
            </label>
            )}
          </div>
          <p className={styles.idPreview}>
            {t("team.willCreate", "将创建")} <code>{createIdPreview}</code>
          </p>
          {createForm.kind === "org" ? (
            <p className={styles.orgNote}>
              组织策略写入本机共享层（~/.deepcode/team），对这台机器上的所有项目生效；红线条目不可被下层覆盖。
            </p>
          ) : null}
          {createError ? (
            <p className={styles.formError}>{createError}</p>
          ) : null}
        </Modal>
      ) : null}

      {entryDraft ? (
        <Modal
          title={
            entryDraft.entry
              ? t("team.editKnowledge", "编辑知识")
              : t("team.addKnowledge", "添加知识")
          }
          onClose={() => setEntryDraft(null)}
          footer={
            <>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => setEntryDraft(null)}
                disabled={entryBusy}
              >
                {t("team.cancel", "取消")}
              </button>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => void submitEntry(false)}
                disabled={entryBusy}
              >
                {t("team.saveDraft", "保存草稿")}
              </button>
              <button
                type="button"
                className={styles.primary}
                onClick={() => void submitEntry(true)}
                disabled={entryBusy}
              >
                {t("team.saveAndReview", "保存并标记已评审")}
              </button>
            </>
          }
        >
          <div className={styles.formGrid}>
            <label>
              <span>{t("team.type", "类型")}</span>
              <select
                value={entryDraft.form.type}
                onChange={(event) =>
                  setEntryDraft({
                    ...entryDraft,
                    form: { ...entryDraft.form, type: event.target.value },
                  })
                }
              >
                {(selectedEntity?.kind === "org"
                  ? ORG_POLICY_TYPES
                  : KNOWLEDGE_TYPES
                ).map((type) => (
                  <option key={type} value={type}>
                    {TYPE_LABELS[type]}（{type}）
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.wide}>
              <span>{t("team.statement", "陈述")}</span>
              <textarea
                autoFocus
                rows={3}
                value={entryDraft.form.statement}
                onChange={(event) =>
                  setEntryDraft({
                    ...entryDraft,
                    form: { ...entryDraft.form, statement: event.target.value },
                  })
                }
              />
            </label>
            <label className={styles.wide}>
              <span>{t("team.rationale", "理由")}</span>
              <textarea
                rows={2}
                value={entryDraft.form.rationale}
                onChange={(event) =>
                  setEntryDraft({
                    ...entryDraft,
                    form: { ...entryDraft.form, rationale: event.target.value },
                  })
                }
              />
            </label>
            <label className={styles.wide}>
              <span>{t("team.evidence", "证据")}</span>
              <textarea
                rows={2}
                value={entryDraft.form.evidence}
                onChange={(event) =>
                  setEntryDraft({
                    ...entryDraft,
                    form: { ...entryDraft.form, evidence: event.target.value },
                  })
                }
              />
            </label>
            <label>
              <span>{t("team.confidence", "置信度")}</span>
              <div className={styles.segmented}>
                {(["high", "medium", "low"] as const).map((level) => (
                  <button
                    key={level}
                    type="button"
                    data-active={entryDraft.form.confidence === level}
                    onClick={() =>
                      setEntryDraft({
                        ...entryDraft,
                        form: { ...entryDraft.form, confidence: level },
                      })
                    }
                  >
                    {CONFIDENCE_LABELS[level]}
                  </button>
                ))}
              </div>
            </label>
            <label>
              <span>{t("team.owner", "所有者")}</span>
              <input
                value={entryDraft.form.owner}
                onChange={(event) =>
                  setEntryDraft({
                    ...entryDraft,
                    form: { ...entryDraft.form, owner: event.target.value },
                  })
                }
              />
            </label>
          </div>
          {entryError ? <p className={styles.formError}>{entryError}</p> : null}
        </Modal>
      ) : null}

      {preview ? (
        <Modal
          title={t("team.previewTitle", "装配预览")}
          onClose={() => setPreview(null)}
          wide
          footer={
            <>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => void copyPreview()}
              >
                <Copy size={13} />
                {copied ? t("team.copied", "已复制") : t("team.copy", "复制")}
              </button>
              <button
                type="button"
                className={styles.primary}
                onClick={() => setPreview(null)}
              >
                {t("team.close", "关闭")}
              </button>
            </>
          }
        >
          <div className={styles.stats}>
            <span>
              {t("team.entries", "条目")}: {preview.entryCount}
            </span>
            <span>
              {t("team.chainLength", "链上实体")}: {preview.chain.length}
            </span>
            {preview.externalExcluded > 0 ? (
              <span>
                {t("team.externalExcluded", "未纳入外部条目")}:{" "}
                {preview.externalExcluded}
              </span>
            ) : null}
            {preview.truncated ? (
              <span className={styles.warn}>
                {t("team.truncated", "已按预算截断")}
              </span>
            ) : null}
          </div>
          <pre className={styles.previewText}>{preview.text}</pre>
          {preview.blockedOverrides.length > 0 ? (
            <p className={styles.formError}>
              {t("team.blocked", "被拒绝的覆盖")}:{" "}
              {preview.blockedOverrides.join("、")}
            </p>
          ) : null}
        </Modal>
      ) : null}
    </section>
  );
}


function OrgPolicyEditor({
  knowledge,
  unlocked,
  busy,
  onAdd,
  onSave,
  onPromote,
  onRemove,
}: {
  knowledge: TeamKnowledgeView[];
  unlocked: boolean;
  busy: boolean;
  onAdd: (type: string, statement: string) => void;
  onSave: (entry: TeamKnowledgeView, statement: string) => void;
  onPromote: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [rows, setRows] = useState<Record<string, string>>({});
  const [confirmId, setConfirmId] = useState<string | null>(null);

  return (
    <div className={styles.policy}>
      {ORG_SECTIONS.map(([type, label]) => {
        const entries = knowledge.filter((entry) => entry.type === type);
        return (
          <section key={type} className={styles.policySection}>
            <h4>{label}</h4>
            {entries.length === 0 ? (
              <p className={styles.muted}>暂无条目</p>
            ) : (
              entries.map((entry) => {
                const draft = edits[entry.id] ?? entry.statement;
                const editable = unlocked && entry.status === "draft";
                return (
                  <div key={entry.id} className={styles.policyRow}>
                    {editable ? (
                      <input
                        value={draft}
                        onChange={(event) =>
                          setEdits({ ...edits, [entry.id]: event.target.value })
                        }
                      />
                    ) : (
                      <span>{entry.statement}</span>
                    )}
                    <span
                      className={styles.statusChip}
                      data-status={entry.status}
                    >
                      {STATUS_LABELS[entry.status] ?? entry.status}
                    </span>
                    {editable ? (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => onSave(entry, draft)}
                        >
                          保存
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => onPromote(entry.id)}
                        >
                          启用
                        </button>
                        {confirmId === entry.id ? (
                          <button
                            type="button"
                            className={styles.danger}
                            disabled={busy}
                            onClick={() => {
                              onRemove(entry.id);
                              setConfirmId(null);
                            }}
                          >
                            确认删除
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => setConfirmId(entry.id)}
                          >
                            删除
                          </button>
                        )}
                      </>
                    ) : null}
                  </div>
                );
              })
            )}
            {unlocked ? (
              <div className={styles.policyRow}>
                <input
                  value={rows[type] ?? ""}
                  placeholder={`添加${label}条目…`}
                  onChange={(event) =>
                    setRows({ ...rows, [type]: event.target.value })
                  }
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      onAdd(type, rows[type] ?? "");
                      setRows({ ...rows, [type]: "" });
                    }
                  }}
                />
                <button
                  type="button"
                  disabled={busy || !(rows[type] ?? "").trim()}
                  onClick={() => {
                    onAdd(type, rows[type] ?? "");
                    setRows({ ...rows, [type]: "" });
                  }}
                >
                  添加
                </button>
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

function Modal({
  title,
  onClose,
  footer,
  wide,
  children,
}: {
  title: string;
  onClose: () => void;
  footer: ReactNode;
  wide?: boolean;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className={styles.backdrop}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={styles.dialog}
        data-wide={wide ? "true" : undefined}
      >
        <header className={styles.dialogHead}>
          <h3>{title}</h3>
          <button type="button" onClick={onClose} aria-label="关闭">
            <X size={15} />
          </button>
        </header>
        <div className={styles.dialogBody}>{children}</div>
        <footer className={styles.dialogFoot}>{footer}</footer>
      </div>
    </div>
  );
}
