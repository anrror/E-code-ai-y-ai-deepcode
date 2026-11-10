import { useCallback, useEffect, useMemo, useState } from "react";
import { Eye, Layers, Plus, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import type {
  Project,
  TeamEntityView,
  TeamKnowledgeView,
} from "../../generated/app-server";
import type { ClientRuntime } from "../../rpc/contracts";

import styles from "./TeamPage.module.css";

const KIND_ORDER = [
  "org",
  "line",
  "product",
  "project",
  "batch",
  "version",
  "task",
] as const;

const KNOWLEDGE_TYPES = [
  "redline",
  "decision",
  "convention",
  "domain",
  "design",
  "lesson",
  "external",
] as const;

interface TeamTreeResult {
  bind: string | null;
  inChain: string[];
  entities: TeamEntityView[];
}

interface TeamReadResult {
  entity: TeamEntityView;
  instructions: string;
  knowledge: TeamKnowledgeView[];
}

interface TeamPreviewResult {
  task: string | null;
  chain: string[];
  referenced: string[];
  entryCount: number;
  externalExcluded: number;
  blockedOverrides: string[];
  truncated: boolean;
  text: string;
}

interface TeamPageProps {
  runtime: ClientRuntime;
  project: Project | null;
}

export function TeamPage({ runtime, project }: TeamPageProps) {
  const { t } = useTranslation();
  const [tree, setTree] = useState<TeamTreeResult | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TeamReadResult | null>(null);
  const [preview, setPreview] = useState<TeamPreviewResult | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    kind: "project",
    slug: "",
    name: "",
    parent: "",
  });
  const [knowledgeForm, setKnowledgeForm] = useState({
    type: "decision",
    statement: "",
    owner: "",
  });
  const [formError, setFormError] = useState<string | null>(null);

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
    } catch (error) {
      setTree(null);
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  }, [project, runtime]);

  const loadDetail = useCallback(
    async (entityId: string) => {
      if (!project) return;
      setBusy(true);
      try {
        const result = await runtime.request("team/read", {
          projectId: project.id,
          entityId,
        });
        setDetail(result);
      } catch (error) {
        setDetail(null);
        setStatus(String(error));
      } finally {
        setBusy(false);
      }
    },
    [project, runtime],
  );

  useEffect(() => {
    setDetail(null);
    setPreview(null);
    setSelectedId(null);
    void loadTree();
  }, [loadTree]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  const groups = useMemo(() => {
    const byKind = new Map<string, TeamEntityView[]>();
    for (const entity of tree?.entities ?? []) {
      const list = byKind.get(entity.kind) ?? [];
      list.push(entity);
      byKind.set(entity.kind, list);
    }
    return KIND_ORDER.filter((kind) => byKind.has(kind)).map((kind) => ({
      kind,
      entities: byKind.get(kind) ?? [],
    }));
  }, [tree]);

  const createEntity = async () => {
    if (!project) return;
    const slug = createForm.slug.trim();
    if (!slug) {
      setFormError(t("team.slugRequired", "A folder name is required."));
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const entityId = `${createForm.kind}://${slug}`;
      await runtime.request("team/create", {
        projectId: project.id,
        kind: createForm.kind,
        slug,
        name: createForm.name.trim() || undefined,
        parent: createForm.parent.trim() || undefined,
      });
      setShowCreate(false);
      setCreateForm({ kind: createForm.kind, slug: "", name: "", parent: "" });
      await loadTree();
      setSelectedId(entityId);
    } catch (error) {
      setFormError(String(error));
    } finally {
      setBusy(false);
    }
  };

  const addKnowledge = async () => {
    if (!project || !selectedId) return;
    const statement = knowledgeForm.statement.trim();
    if (!statement) {
      setFormError(t("team.statementRequired", "A statement is required."));
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      await runtime.request("team/knowledge/add", {
        projectId: project.id,
        entityId: selectedId,
        type: knowledgeForm.type,
        statement,
        owner: knowledgeForm.owner.trim() || undefined,
      });
      setKnowledgeForm({ ...knowledgeForm, statement: "" });
      await loadDetail(selectedId);
      await loadTree();
    } catch (error) {
      setFormError(String(error));
    } finally {
      setBusy(false);
    }
  };

  const promote = async (knowledgeId: string) => {
    if (!project || !selectedId) return;
    setBusy(true);
    try {
      await runtime.request("team/knowledge/promote", {
        projectId: project.id,
        knowledgeId,
      });
      await loadDetail(selectedId);
      await loadTree();
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  };

  const showPreview = async () => {
    if (!project) return;
    setBusy(true);
    try {
      const result = await runtime.request("team/preview", {
        projectId: project.id,
        entityId: tree?.bind ?? selectedId ?? undefined,
        includeExternal: true,
      });
      setPreview(result);
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  };

  if (!project) {
    return (
      <section className={styles.page}>
        <p className={styles.placeholder}>
          {t("team.openProject", "Open a project to see its team context.")}
        </p>
      </section>
    );
  }

  const isEmpty = !tree || tree.entities.length === 0;

  const createFormView = (
    <form
      className={styles.createForm}
      onSubmit={(event) => {
        event.preventDefault();
        void createEntity();
      }}
    >
      <select
        value={createForm.kind}
        onChange={(event) =>
          setCreateForm({ ...createForm, kind: event.target.value })
        }
      >
        {KIND_ORDER.map((kind) => (
          <option key={kind} value={kind}>
            {kind}
          </option>
        ))}
      </select>
      <input
        value={createForm.slug}
        placeholder={t("team.slugPlaceholder", "Folder name, e.g. changan")}
        onChange={(event) =>
          setCreateForm({ ...createForm, slug: event.target.value })
        }
      />
      <input
        value={createForm.name}
        placeholder={t("team.namePlaceholder", "Display name")}
        onChange={(event) =>
          setCreateForm({ ...createForm, name: event.target.value })
        }
      />
      <input
        value={createForm.parent}
        placeholder={t("team.parentPlaceholder", "Parent id (optional)")}
        onChange={(event) =>
          setCreateForm({ ...createForm, parent: event.target.value })
        }
      />
      <button type="submit" disabled={busy}>
        <Plus size={14} />
        {t("team.create", "Create layer")}
      </button>
    </form>
  );

  return (
    <section className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>{t("team.eyebrow", "Team context")}</p>
          <h2>{project.displayName}</h2>
          <p className={styles.hint}>
            {t(
              "team.readonlyHint",
              "Layers below the task are optional: a missing layer adds nothing. Choose a layer, then add knowledge; drafts become effective once reviewed.",
            )}
          </p>
        </div>
        <div className={styles.headerActions}>
          <button
            type="button"
            className={styles.iconButton}
            onClick={() => setShowCreate((value) => !value)}
            title={t("team.newLayer", "New layer")}
          >
            <Plus size={16} />
          </button>
          <button
            type="button"
            className={styles.iconButton}
            disabled={busy}
            onClick={() => void loadTree()}
            title={t("team.refresh", "Refresh")}
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </header>

      {showCreate || isEmpty ? createFormView : null}
      {formError ? <p className={styles.status}>{formError}</p> : null}

      <div className={styles.body}>
        <aside className={styles.sidebar}>
          {tree?.bind ? (
            <p className={styles.bound}>
              <Layers size={14} />
              <code>{tree.bind}</code>
            </p>
          ) : (
            <p className={styles.empty}>
              {t("team.unbound", "This workspace does not bind to a task yet.")}
            </p>
          )}

          {groups.map((group) => (
            <section key={group.kind} className={styles.group}>
              <h3>{group.kind}</h3>
              <ul className={styles.files}>
                {group.entities.map((entity) => (
                  <li key={entity.id}>
                    <button
                      type="button"
                      data-active={selectedId === entity.id}
                      onClick={() => setSelectedId(entity.id)}
                      title={entity.id}
                    >
                      <span>{entity.name}</span>
                      <em>
                        {entity.knowledgeCount}
                        {entity.draftCount > 0 ? ` · ${entity.draftCount}d` : ""}
                        {entity.external ? " · ext" : ""}
                        {entity.archived ? " · hist" : ""}
                      </em>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}

          {isEmpty ? (
            <p className={styles.empty}>
              {t(
                "team.empty",
                "No layers configured yet. Create one above; it writes .deepcode/team/<layer>/<folder>/.",
              )}
            </p>
          ) : null}
        </aside>

        <main className={styles.editor}>
          {detail ? (
            <>
              <div className={styles.editorBar}>
                <code>{detail.entity.id}</code>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void showPreview()}
                >
                  <Eye size={14} />
                  {t("team.preview", "Preview context")}
                </button>
              </div>

              <dl className={styles.meta}>
                <dt>{t("team.kind", "Layer")}</dt>
                <dd>{detail.entity.kind}</dd>
                <dt>{t("team.parent", "Parent")}</dt>
                <dd>{detail.entity.parent ?? "—"}</dd>
                <dt>{t("team.workspace", "Workspace")}</dt>
                <dd>{detail.entity.workspace ?? "—"}</dd>
                <dt>{t("team.references", "References")}</dt>
                <dd>{detail.entity.references?.join(", ") || "—"}</dd>
                <dt>{t("team.codebases", "Codebases")}</dt>
                <dd>{detail.entity.codebases?.join(", ") || "—"}</dd>
                <dt>{t("team.designs", "Designs")}</dt>
                <dd>{detail.entity.designs?.join(", ") || "—"}</dd>
              </dl>

              {detail.instructions ? (
                <section className={styles.textBlock}>
                  <h4>{t("team.instructions", "AGENTS.md")}</h4>
                  <pre>{detail.instructions}</pre>
                </section>
              ) : null}

              <section className={styles.textBlock}>
                <h4>
                  {t("team.knowledge", "Knowledge")} ({detail.knowledge.length})
                </h4>
                {detail.knowledge.length === 0 ? (
                  <p className={styles.placeholder}>
                    {t("team.noKnowledge", "No entries in this layer.")}
                  </p>
                ) : (
                  <table className={styles.knowledge}>
                    <thead>
                      <tr>
                        <th>{t("team.type", "Type")}</th>
                        <th>{t("team.statement", "Statement")}</th>
                        <th>{t("team.confidence", "Confidence")}</th>
                        <th>{t("team.status", "Status")}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {detail.knowledge.map((entry) => (
                        <tr key={entry.id}>
                          <td>{entry.type}</td>
                          <td>{entry.statement}</td>
                          <td>{entry.confidence}</td>
                          <td>{entry.status}</td>
                          <td>
                            {entry.status === "draft" ? (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void promote(entry.id)}
                              >
                                {t("team.markReviewed", "Mark reviewed")}
                              </button>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

              <form
                className={styles.knowledgeForm}
                onSubmit={(event) => {
                  event.preventDefault();
                  void addKnowledge();
                }}
              >
                <select
                  value={knowledgeForm.type}
                  onChange={(event) =>
                    setKnowledgeForm({
                      ...knowledgeForm,
                      type: event.target.value,
                    })
                  }
                >
                  {KNOWLEDGE_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
                <input
                  value={knowledgeForm.statement}
                  placeholder={t(
                    "team.statementPlaceholder",
                    "What should the agent know?",
                  )}
                  onChange={(event) =>
                    setKnowledgeForm({
                      ...knowledgeForm,
                      statement: event.target.value,
                    })
                  }
                />
                <input
                  value={knowledgeForm.owner}
                  placeholder={t("team.ownerPlaceholder", "Owner")}
                  onChange={(event) =>
                    setKnowledgeForm({
                      ...knowledgeForm,
                      owner: event.target.value,
                    })
                  }
                />
                <button type="submit" disabled={busy}>
                  <Plus size={14} />
                  {t("team.addKnowledge", "Add knowledge")}
                </button>
              </form>

              {preview ? (
                <section className={styles.textBlock}>
                  <h4>
                    {t("team.assembled", "Assembled context")} ·{" "}
                    {preview.entryCount} {t("team.entries", "entries")}
                    {preview.truncated ? " · truncated" : ""}
                  </h4>
                  <pre className={styles.assembled}>{preview.text}</pre>
                  {preview.blockedOverrides.length > 0 ? (
                    <p className={styles.warning}>
                      {t("team.blocked", "Rejected overrides: ")}
                      {preview.blockedOverrides.join(", ")}
                    </p>
                  ) : null}
                </section>
              ) : null}
            </>
          ) : (
            <p className={styles.placeholder}>
              {t("team.select", "Select a layer to inspect it.")}
            </p>
          )}
          {status ? <p className={styles.status}>{status}</p> : null}
        </main>
      </div>
    </section>
  );
}
