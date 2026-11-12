import { Fragment, Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Archive,
  Check,
  ChevronDown,
  Copy,
  Eye,
  EyeOff,
  Layers,
  Lock,
  Shield,
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
import { useSystemDarkMode } from "../../app/useSystemDarkMode";
import { languageFor } from "../inspector/inspectorFormat";

import styles from "./TeamPage.module.css";

const LocalMonacoEditor = lazy(() => import("../workbench/LocalMonacoEditor"));

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
  project: ["line", "product"],
  batch: ["project"],
  version: ["batch"],
  task: ["version"],
};

const CHILD_KINDS: Record<string, Kind[]> = {
  org: ["line", "product"],
  line: ["product", "project"],
  product: ["project"],
  project: ["batch"],
  batch: ["version"],
  version: ["task"],
  task: [],
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
  onProjectsChanged?: () => void;
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

export function TeamPage({
  runtime,
  project,
  onProjectsChanged,
}: TeamPageProps) {
  const { t } = useTranslation();
  const projectId = project?.id;
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
  const [editWorkspace, setEditWorkspace] = useState("");
  const [editCodebases, setEditCodebases] = useState("");
  const [editDesigns, setEditDesigns] = useState("");
  const [editReferences, setEditReferences] = useState<string[]>([]);
  const [projects, setProjects] = useState<
    { id: string; canonicalPath: string; displayName: string }[]
  >([]);
  const [showVisibility, setShowVisibility] = useState(false);
  const [envInfo, setEnvInfo] = useState<
    { environments: { name: string; keys: string[] }[]; active: string | null } | null
  >(null);
  const [envName, setEnvName] = useState("");
  const [envText, setEnvText] = useState("");
  const [newEnvName, setNewEnvName] = useState("");
  const [browser, setBrowser] = useState<
    {
      root: string;
      cwd: string;
      entries: { name: string; path: string; type: string; size: number }[];
      file: { path: string; content: string; truncated: boolean } | null;
    } | null
  >(null);
  const darkMode = useSystemDarkMode();
  const [hiddenPaths, setHiddenPaths] = useState<Set<string>>(new Set());
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());

  const loadTree = useCallback(async () => {
    setBusy(true);
    try {
      const result = await runtime.request("team/tree", {
        ...(projectId ? { projectId } : {}),
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
  }, [projectId, runtime]);

  const loadVisibility = useCallback(async () => {
    try {
      const result = await runtime.request("team/visibility/read", {});
      setHiddenPaths(new Set(result.hidden));
    } catch {
      setHiddenPaths(new Set());
    }
  }, [runtime]);

  const loadProjects = useCallback(async () => {
    try {
      const result = await runtime.request("project/list", { limit: 200 });
      setProjects(result.projects);
    } catch {
      setProjects([]);
    }
  }, [runtime]);

  const loadDetail = useCallback(
    async (entityId: string) => {
        try {
        const result = await runtime.request("team/read", {
          ...(projectId ? { projectId } : {}),
          entityId,
        });
        setDetail(result);
      } catch (caught) {
        setDetail(null);
        setError(String(caught));
      }
    },
    [projectId, runtime],
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- page load
    void loadTree();
  }, [loadTree]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- visibility load
    void loadVisibility();
  }, [loadVisibility]);

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

  const pathEntities = useMemo(() => {
    if (!tree) return [];
    const byId = new Map(tree.entities.map((entity) => [entity.id, entity]));
    if (tree.inChain.length > 0) {
      return tree.inChain
        .map((id) => byId.get(id))
        .filter((entity): entity is TeamEntityView => Boolean(entity));
    }
    const chain: TeamEntityView[] = [];
    const seen = new Set<string>();
    let cursor = selectedId ? byId.get(selectedId) : undefined;
    while (cursor && !seen.has(cursor.id)) {
      seen.add(cursor.id);
      chain.unshift(cursor);
      cursor = cursor.parent ? byId.get(cursor.parent) : undefined;
    }
    return chain;
  }, [tree, selectedId]);

  const childrenByParent = useMemo(() => {
    const byParent = new Map<string | null, TeamEntityView[]>();
    const known = new Set((tree?.entities ?? []).map((entity) => entity.id));
    const orgIds = new Set(
      (tree?.entities ?? [])
        .filter((entity) => entity.kind === "org")
        .map((entity) => entity.id),
    );
    for (const entity of tree?.entities ?? []) {
      if (entity.kind === "org") continue;
      const key =
        entity.parent && known.has(entity.parent) && !orgIds.has(entity.parent)
          ? entity.parent
          : null;
      const list = byParent.get(key) ?? [];
      list.push(entity);
      byParent.set(key, list);
    }
    for (const list of byParent.values()) {
      list.sort(
        (left, right) =>
          kindIndex(left.kind) - kindIndex(right.kind) ||
          left.name.localeCompare(right.name),
      );
    }
    return byParent;
  }, [tree]);

  const nextChildKind = (kind: string): Kind | null =>
    CHILD_KINDS[kind]?.[0] ?? null;

  const renderBranch = (parent: string | null, depth: number): ReactNode =>
    (childrenByParent.get(parent) ?? []).map((entity) => {
      const children = childrenByParent.get(entity.id) ?? [];
      const collapsed = collapsedIds.has(entity.id);
      const childKind = nextChildKind(entity.kind);
      return (
        <div key={entity.id}>
          <div
            className={styles.treeRow}
            data-active={entity.id === selectedId}
            style={{ paddingLeft: 4 + depth * 14 }}
          >
            <button
              type="button"
              className={styles.treeChevron}
              disabled={children.length === 0}
              aria-label={collapsed ? "展开" : "折叠"}
              onClick={() => {
                const next = new Set(collapsedIds);
                if (collapsed) next.delete(entity.id);
                else next.add(entity.id);
                setCollapsedIds(next);
              }}
            >
              {children.length === 0 ? (
                <span className={styles.treeDot} />
              ) : (
                <ChevronDown
                  size={12}
                  style={{
                    transform: collapsed ? "rotate(-90deg)" : undefined,
                  }}
                />
              )}
            </button>
            <button
              type="button"
              className={styles.treeLabel}
              onClick={() => setSelectedId(entity.id)}
              title={entity.id}
            >
              <span className={styles.treeKind}>
                {KIND_LABELS[entity.kind as Kind] ?? entity.kind}
              </span>
              <span className={styles.treeName}>{entity.name}</span>
              {entity.draftCount > 0 ? (
                <span className={styles.treeDraft}>{entity.draftCount}</span>
              ) : null}
              <span className={styles.treeCount}>{entity.knowledgeCount}</span>
            </button>
            {entity.kind === "project" && entity.workspace ? (
              <button
                type="button"
                className={styles.treeAdd}
                aria-label={
                  hiddenPaths.has(entity.workspace)
                    ? "在侧栏显示"
                    : "在侧栏隐藏"
                }
                title={
                  hiddenPaths.has(entity.workspace)
                    ? "在侧栏显示"
                    : "在侧栏隐藏"
                }
                onClick={() =>
                  void toggleVisibility(
                    entity.workspace as string,
                    hiddenPaths.has(entity.workspace as string),
                  )
                }
              >
                {hiddenPaths.has(entity.workspace) ? (
                  <EyeOff size={11} />
                ) : (
                  <Eye size={11} />
                )}
              </button>
            ) : null}
            {childKind ? (
              <button
                type="button"
                className={styles.treeAdd}
                aria-label={`添加${KIND_LABELS[childKind]}`}
                title={`添加${KIND_LABELS[childKind]}`}
                onClick={() => openCreate(childKind, entity.id)}
              >
                <Plus size={11} />
              </button>
            ) : null}
          </div>
          {collapsed ? null : renderBranch(entity.id, depth + 1)}
        </div>
      );
    });

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
    const allowed = PARENT_KINDS[createForm.kind] ?? [];
    return (tree?.entities ?? []).filter((entity) =>
      allowed.includes(entity.kind),
    );
  }, [createForm, tree]);

  const loadQueue = useCallback(async () => {
    if (!tree) return;
    setQueueLoading(true);
    try {
      const details = await Promise.all(
        tree.entities.map((entity) =>
          runtime.request("team/read", {
            ...(projectId ? { projectId } : {}),
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
  }, [projectId, runtime, tree]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- queue is fetched when opened
    if (queueMode) void loadQueue();
  }, [queueMode, loadQueue]);

  const openCreate = (kind: Kind, parentId?: string) => {
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
    const allowed = PARENT_KINDS[kind] ?? [];
    const parent =
      parentId ??
      (tree?.inChain ?? [])
        .map((id) => tree?.entities.find((entity) => entity.id === id))
        .filter((entity): entity is TeamEntityView => Boolean(entity))
        .filter((entity) => allowed.includes(entity.kind))
        .pop()?.id ??
      "";
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
        ...(projectId ? { projectId } : {}),
        kind: createForm.kind,
        slug,
        name: name || undefined,
        parent:
          parentOptions.some((entity) => entity.id === createForm.parent)
            ? createForm.parent
            : undefined,
        external: createForm.external,
        shared: true,
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
    if (!selectedId || !entryDraft) return;
    const statement = entryDraft.form.statement.trim();
    if (!statement) {
      setEntryError(t("team.statementRequired", "请填写陈述内容"));
      return;
    }
    setEntryBusy(true);
    setEntryError(null);
    try {
      const shared = true;
      const params = {
        ...(projectId ? { projectId } : {}),
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
          ...(projectId ? { projectId } : {}),
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
    if (!selectedId) return;
    setBusy(true);
    try {
      await runtime.request("team/knowledge/promote", {
        ...(projectId ? { projectId } : {}),
        knowledgeId,
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

  const removeEntry = async (knowledgeId: string) => {
    if (!selectedId) return;
    setBusy(true);
    try {
      await runtime.request("team/knowledge/remove", {
        ...(projectId ? { projectId } : {}),
        knowledgeId,
        shared: true,
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
        ...(projectId ? { projectId } : {}),
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
        ...(projectId ? { projectId } : {}),
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
    if (!selectedId) return;
    setBusy(true);
    try {
      await runtime.request("team/entity/update", {
        ...(projectId ? { projectId } : {}),
        entityId: selectedId,
        name: editName.trim() || undefined,
        parent: editParent,
        external: editExternal,
        workspace: editWorkspace.trim(),
        codebases: editCodebases
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
        designs: editDesigns
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
        references: editReferences,
        shared: true,
      });
      setEditing(false);
      await loadTree();
      await loadDetail(selectedId);
      const folder = editWorkspace.trim();
      if (folder && !projects.some((item) => item.canonicalPath === folder)) {
        try {
          await runtime.request("project/add", {
            path: folder,
            displayName: editName.trim() || undefined,
          });
          await loadProjects();
          onProjectsChanged?.();
        } catch (caught) {
          setError(`层已保存，但添加项目失败：${String(caught)}`);
        }
      }
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const deleteLayer = async () => {
    if (!selectedId) return;
    const entity = tree?.entities.find((item) => item.id === selectedId);
    setBusy(true);
    try {
      await runtime.request("team/entity/remove", {
        ...(projectId ? { projectId } : {}),
        entityId: selectedId,
        shared: true,
        force: (entity?.knowledgeCount ?? 0) > 0,
      });
      setEditing(false);
      setDeleteArmed(false);
      setSelectedId(null);
      await loadTree();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const deprecate = async (knowledgeId: string) => {
    setBusy(true);
    try {
      await runtime.request("team/knowledge/deprecate", {
        ...(projectId ? { projectId } : {}),
        knowledgeId,
        shared: true,
      });
      if (selectedId) await loadDetail(selectedId);
      await loadTree();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const readEnv = useCallback(
    async (entityId: string) => {
      try {
        const info = await runtime.request("team/env/read", { entityId });
        setEnvInfo(info);
        const first = info.active ?? info.environments[0]?.name ?? "";
        setEnvName(first);
        if (first) {
          const values = await runtime.request("team/env/values", {
            entityId,
            environment: first,
          });
          setEnvText(
            Object.entries(values.values)
              .map(([key, value]) => `${key}=${value}`)
              .join("\n"),
          );
        } else {
          setEnvText("");
        }
      } catch (caught) {
        setError(String(caught));
      }
    },
    [runtime],
  );

  useEffect(() => {
    if (detail && detail.entity.kind === "project") {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- environment load
      void readEnv(detail.entity.id);
    }
  }, [detail, readEnv]);

  const openEnv = async (name: string) => {
    if (!selectedEntity) return;
    setEnvName(name);
    try {
      const values = await runtime.request("team/env/values", {
        entityId: selectedEntity.id,
        environment: name,
      });
      setEnvText(
        Object.entries(values.values)
          .map(([key, value]) => `${key}=${value}`)
          .join("\n"),
      );
    } catch (caught) {
      setError(String(caught));
    }
  };

  const saveEnv = async () => {
    if (!selectedEntity || !envName) return;
    setBusy(true);
    try {
      const values: Record<string, string> = {};
      for (const line of envText.split("\n")) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#")) continue;
        const index = trimmed.indexOf("=");
        if (index <= 0) continue;
        values[trimmed.slice(0, index).trim()] = trimmed.slice(index + 1);
      }
      await runtime.request("team/env/save", {
        entityId: selectedEntity.id,
        environment: envName,
        values,
      });
      await readEnv(selectedEntity.id);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const createEnv = async () => {
    if (!selectedEntity || !newEnvName.trim()) return;
    setBusy(true);
    try {
      await runtime.request("team/env/save", {
        entityId: selectedEntity.id,
        environment: newEnvName.trim(),
        values: {},
      });
      const name = newEnvName.trim();
      setNewEnvName("");
      await readEnv(selectedEntity.id);
      await openEnv(name);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const activateEnv = async () => {
    if (!selectedEntity || !envName) return;
    setBusy(true);
    try {
      await runtime.request("team/env/activate", {
        entityId: selectedEntity.id,
        environment: envName,
      });
      await readEnv(selectedEntity.id);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const browseEntity = detail?.entity ?? null;
  const browseRoots = browseEntity
    ? [browseEntity.workspace, ...(browseEntity.codebases ?? []), ...(browseEntity.designs ?? [])].filter(
        (value): value is string => Boolean(value),
      )
    : [];

  const loadDir = async (root: string, pathValue: string) => {
    if (!browseEntity) return;
    try {
      const result = await runtime.request("team/browse", {
        entityId: browseEntity.id,
        root,
        path: pathValue,
      });
      setBrowser({ root, cwd: result.path, entries: result.entries, file: null });
    } catch (caught) {
      setError(String(caught));
    }
  };

  const openBrowse = async () => {
    if (!browseEntity || browseRoots.length === 0) {
      setError("先在“编辑层信息”里配置代码或文档路径");
      return;
    }
    await loadDir(browseRoots[0], "");
  };

  const openFile = async (root: string, filePath: string) => {
    if (!browseEntity) return;
    try {
      const file = await runtime.request("team/browse/read", {
        entityId: browseEntity.id,
        root,
        path: filePath,
      });
      setBrowser((current) => (current ? { ...current, file } : current));
    } catch (caught) {
      setError(String(caught));
    }
  };

  const openVisibility = async () => {
    setShowVisibility(true);
    await loadProjects();
    try {
      const result = await runtime.request("team/visibility/read", {});
      setHiddenPaths(new Set(result.hidden));
    } catch {
      setHiddenPaths(new Set());
    }
  };

  const toggleVisibility = async (path: string, visible: boolean) => {
    const next = new Set(hiddenPaths);
    if (visible) next.delete(path);
    else next.add(path);
    setHiddenPaths(next);
    try {
      await runtime.request("team/visibility/set", {
        path,
        hidden: !visible,
      });
      onProjectsChanged?.();
    } catch (caught) {
      setError(String(caught));
    }
  };

  const showPreview = async () => {
    setPreviewBusy(true);
    try {
      const result = await runtime.request("team/preview", {
        ...(projectId ? { projectId } : {}),
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

  const entityName = (id: string) =>
    tree?.entities.find((entity) => entity.id === id)?.name ?? id;

  const createIdPreview = createForm
    ? `${createForm.kind}://${createForm.slug.trim() || "…"}`
    : "";

  return (
    <section className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <p className={styles.eyebrow}>{t("team.eyebrow", "团队上下文")}</p>
          <h2>全局团队上下文</h2>
          <p className={styles.binding}>
            {project
              ? `当前项目：${project.displayName}`
              : "未选择项目；显示本机全局库"}
            {tree?.bind ? (
              <>
                {" · "}
                {t("team.boundTask", "当前任务")} <code>{tree.bind}</code>
              </>
            ) : null}
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
            className={styles.ghost}
            onClick={() => void openVisibility()}
          >
            侧栏项目
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

      <button
        type="button"
        className={styles.orgBar}
        data-active={entityForKind("org")?.id === selectedId}
        onClick={() => {
          const org = entityForKind("org");
          if (org) setSelectedId(org.id);
          else openCreate("org");
        }}
      >
        <Shield size={14} />
        <span className={styles.orgTitle}>组织策略</span>
        <span className={styles.orgName}>
          {entityForKind("org")?.name ?? "未配置"}
        </span>
        <span className={styles.orgMeta}>
          {entityForKind("org")
            ? `${entityForKind("org")?.knowledgeCount ?? 0} 条 · 本机全局`
            : "点击配置"}
        </span>
      </button>
      {pathEntities.length > 0 ? (
        <ol className={styles.chain} aria-label={t("team.chain", "上下文链")}>
          {pathEntities.map((entity) => (
            <li key={entity.id} className={styles.chainItem}>
              <button
                type="button"
                className={styles.chainNode}
                data-state="linked"
                data-active={entity.id === selectedId}
                onClick={() => setSelectedId(entity.id)}
                title={entity.id}
              >
                <span className={styles.chainKind}>
                  {KIND_LABELS[entity.kind as Kind] ?? entity.kind}
                </span>
                <span className={styles.chainName}>{entity.name}</span>
                <span className={styles.chainMeta}>
                  {entity.knowledgeCount - entity.draftCount}
                  {entity.draftCount > 0 ? ` · ${entity.draftCount} 草稿` : ""}
                  {entity.external ? " · 外部" : ""}
                  {entity.archived ? " · 历史" : ""}
                </span>
              </button>
            </li>
          ))}
        </ol>
      ) : null}

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
            <div className={styles.layers}>
              {renderBranch(null, 0)}
              {(tree?.entities ?? []).filter(
                (entity) => entity.kind !== "org",
              ).length === 0 ? (
                <p className={styles.muted}>还没有层；点击右上角“新建层”。</p>
              ) : null}
            </div>
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
                      setEditWorkspace(selectedEntity.workspace ?? "");
                      setEditCodebases(
                        (selectedEntity.codebases ?? []).join("\n"),
                      );
                      setEditDesigns((selectedEntity.designs ?? []).join("\n"));
                      setEditReferences(selectedEntity.references ?? []);
                      void loadProjects();
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
                  {browseRoots.length > 0 ? (
                    <button
                      type="button"
                      className={styles.ghost}
                      onClick={() => void openBrowse()}
                    >
                      浏览代码/文档
                    </button>
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
                              ) : entry.status === "reviewed" ? (
                                <button
                                  type="button"
                                  aria-label="弃用"
                                  title="弃用（保留记录，标记为已废弃）"
                                  disabled={busy}
                                  onClick={() => void deprecate(entry.id)}
                                >
                                  <Archive size={13} />
                                </button>
                              ) : (
                                <span
                                  className={styles.locked}
                                  title={t(
                                    "team.lockedHint",
                                    "已废弃条目只读。",
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

                  {selectedEntity.kind === "project" ? (
                    <details className={styles.disclosure} open>
                      <summary>
                        <ChevronDown size={13} />
                        环境变量
                        {envInfo?.active ? ` · 当前 ${envInfo.active}` : ""}
                      </summary>
                      <div className={styles.envPanel}>
                        <div className={styles.envRow}>
                          <select
                            value={envName}
                            onChange={(event) => void openEnv(event.target.value)}
                          >
                            <option value="">选择环境</option>
                            {(envInfo?.environments ?? []).map((item) => (
                              <option key={item.name} value={item.name}>
                                {item.name}（{item.keys.length} 项）
                              </option>
                            ))}
                          </select>
                          <input
                            value={newEnvName}
                            placeholder="新环境，如 dev"
                            onChange={(event) => setNewEnvName(event.target.value)}
                          />
                          <button type="button" onClick={() => void createEnv()}>
                            新建
                          </button>
                          <button
                            type="button"
                            disabled={!envName}
                            onClick={() => void activateEnv()}
                          >
                            设为当前
                          </button>
                        </div>
                        <textarea
                          rows={6}
                          value={envText}
                          placeholder={"DATABASE_NAME=app\nDATABASE_USER=app\nDATABASE_PASSWORD=..."}
                          onChange={(event) => setEnvText(event.target.value)}
                        />
                        <div className={styles.envRow}>
                          <button
                            type="button"
                            disabled={!envName || busy}
                            onClick={() => void saveEnv()}
                          >
                            保存
                          </button>
                          <span className={styles.muted}>
                            仅存本机；不会进入模型上下文。
                          </span>
                        </div>
                      </div>
                    </details>
                  ) : null}

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
                      <dd>
                        {(selectedEntity.references ?? []).length > 0
                          ? (selectedEntity.references ?? [])
                              .map(entityName)
                              .join("、")
                          : "—"}
                      </dd>
                      <dt>{t("team.designs", "设计")}</dt>
                      <dd>{selectedEntity.designs?.join("、") || "—"}</dd>
                      <dt>{t("team.codebases", "代码库")}</dt>
                      <dd>{selectedEntity.codebases?.join("、") || "—"}</dd>
                    </dl>
                  </details>
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

      {browser ? (
        <Modal
          title="浏览代码与文档"
          wide
          onClose={() => setBrowser(null)}
          footer={
            <button
              type="button"
              className={styles.primary}
              onClick={() => setBrowser(null)}
            >
              关闭
            </button>
          }
        >
          <div className={styles.browser}>
            <div className={styles.browserSide}>
              <select
                value={browser.root}
                onChange={(event) => void loadDir(event.target.value, "")}
              >
                {browseRoots.map((root) => (
                  <option key={root} value={root}>
                    {root}
                  </option>
                ))}
              </select>
              <p className={styles.browserPath}>/{browser.cwd}</p>
              <ul className={styles.browserList}>
                {browser.cwd ? (
                  <li>
                    <button
                      type="button"
                      onClick={() =>
                        void loadDir(
                          browser.root,
                          browser.cwd.includes("/")
                            ? browser.cwd.slice(0, browser.cwd.lastIndexOf("/"))
                            : "",
                        )
                      }
                    >
                      ..
                    </button>
                  </li>
                ) : null}
                {browser.entries.map((entry) => (
                  <li key={entry.path}>
                    <button
                      type="button"
                      data-active={
                        entry.type === "file" &&
                        browser.file?.path === entry.path
                      }
                      onClick={() =>
                        entry.type === "dir"
                          ? void loadDir(browser.root, entry.path)
                          : void openFile(browser.root, entry.path)
                      }
                    >
                      <span>{entry.type === "dir" ? "▸" : "·"}</span>
                      {entry.name}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            <div className={styles.browserView}>
              {browser.file ? (
                <Suspense fallback={<p className={styles.muted}>加载编辑器…</p>}>
                  <LocalMonacoEditor
                    height="100%"
                    language={languageFor(browser.file.path)}
                    theme={darkMode ? "vs-dark" : "light"}
                    value={browser.file.content}
                    options={{
                      readOnly: true,
                      minimap: { enabled: false },
                      lineNumbers: "on",
                      scrollBeyondLastLine: false,
                      fontSize: 12,
                    }}
                  />
                </Suspense>
              ) : (
                <p className={styles.muted}>选择左侧文件查看内容</p>
              )}
            </div>
          </div>
        </Modal>
      ) : null}

      {showVisibility ? (
        <Modal
          title="侧栏显示的项目"
          onClose={() => setShowVisibility(false)}
          footer={
            <button
              type="button"
              className={styles.primary}
              onClick={() => setShowVisibility(false)}
            >
              关闭
            </button>
          }
        >
          <p className={styles.muted}>
            关闭后该项目只从左侧栏隐藏，项目本身与团队上下文不受影响。
          </p>
          <div className={styles.visibilityList}>
            {projects.map((item) => {
              const visible = !hiddenPaths.has(item.canonicalPath);
              return (
                <label key={item.id} className={styles.visibilityRow}>
                  <input
                    type="checkbox"
                    checked={visible}
                    onChange={(event) =>
                      void toggleVisibility(
                        item.canonicalPath,
                        event.target.checked,
                      )
                    }
                  />
                  <span>{item.displayName}</span>
                  <em>{item.canonicalPath}</em>
                </label>
              );
            })}
          </div>
        </Modal>
      ) : null}

      {editing && selectedEntity ? (
        <Modal
          title="编辑层信息"
          onClose={() => {
            setEditing(false);
            setDeleteArmed(false);
          }}
          footer={
            <>
              <button
                type="button"
                className={styles.dangerButton}
                disabled={busy}
                onClick={() => {
                  if (deleteArmed) void deleteLayer();
                  else setDeleteArmed(true);
                }}
              >
                {deleteArmed
                  ? `确认删除${
                      selectedEntity.knowledgeCount > 0
                        ? `（含 ${selectedEntity.knowledgeCount} 条知识）`
                        : ""
                    }`
                  : "删除层"}
              </button>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => {
                  setEditing(false);
                  setDeleteArmed(false);
                }}
                disabled={busy}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.primary}
                disabled={busy}
                onClick={() => void saveLayer()}
              >
                保存
              </button>
            </>
          }
        >
          <div className={styles.formGrid}>
            <label className={styles.wide}>
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
            {selectedEntity.kind === "project" ? (
              <>
                <label className={styles.wide}>
                  <span>关联项目</span>
                  <select
                    value={editWorkspace}
                    onChange={(event) => setEditWorkspace(event.target.value)}
                  >
                    <option value="">不关联</option>
                    {projects.map((item) => (
                      <option key={item.id} value={item.canonicalPath}>
                        {item.displayName} — {item.canonicalPath}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={styles.wide}>
                  <span>项目文件夹</span>
                  <input
                    value={editWorkspace}
                    placeholder="D:\work\project"
                    onChange={(event) => setEditWorkspace(event.target.value)}
                  />
                </label>
              </>
            ) : null}
            {selectedEntity.kind === "project" ||
            selectedEntity.kind === "product" ? (
              <>
                <label className={styles.wide}>
                  <span>代码路径（文件夹或 Git 地址，每行一个）</span>
                  <textarea
                    rows={3}
                    value={editCodebases}
                    placeholder={"D:/work/service/src\nhttps://git.example.com/team/service.git"}
                    onChange={(event) => setEditCodebases(event.target.value)}
                  />
                </label>
                <label className={styles.wide}>
                  <span>文档路径（文件夹或 URL，每行一个）</span>
                  <textarea
                    rows={3}
                    value={editDesigns}
                    placeholder={"D:/work/service/docs\nhttps://git.example.com/team/docs.git"}
                    onChange={(event) => setEditDesigns(event.target.value)}
                  />
                </label>
                <label className={styles.wide}>
                  <span>关联其他产品 / 项目（调用其接口，不覆盖本层）</span>
                  <div className={styles.referenceList}>
                    {(tree?.entities ?? [])
                      .filter(
                        (entity) =>
                          entity.id !== selectedEntity.id &&
                          (entity.kind === "product" ||
                            entity.kind === "project"),
                      )
                      .map((entity) => (
                        <label
                          key={entity.id}
                          className={styles.referenceRow}
                        >
                          <input
                            type="checkbox"
                            checked={editReferences.includes(entity.id)}
                            onChange={(event) =>
                              setEditReferences((current) =>
                                event.target.checked
                                  ? [...current, entity.id]
                                  : current.filter((id) => id !== entity.id),
                              )
                            }
                          />
                          <span>{entity.name}</span>
                          <em>
                            {KIND_LABELS[entity.kind as Kind] ?? entity.kind}
                          </em>
                        </label>
                      ))}
                  </div>
                </label>
              </>
            ) : null}
            <label className={styles.checkbox}>
              <input
                type="checkbox"
                checked={editExternal}
                onChange={(event) => setEditExternal(event.target.checked)}
              />
              <span>外部（未验证来源）</span>
            </label>
          </div>
          {deleteArmed ? (
            <p className={styles.formError}>
              删除不可恢复；有子层时需先处理子层。
            </p>
          ) : null}
        </Modal>
      ) : null}

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
                    parent: "",
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
