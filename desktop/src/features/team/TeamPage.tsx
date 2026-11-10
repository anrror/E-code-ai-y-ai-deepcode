import { useCallback, useEffect, useMemo, useState } from "react";
import { Eye, Layers, RefreshCw } from "lucide-react";
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
      setStatus(result.entities.length === 0 ? null : null);
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

  useEffect(() => {
    setDetail(null);
    setPreview(null);
    setSelectedId(null);
    void loadTree();
  }, [loadTree]);

  useEffect(() => {
    if (!project || !selectedId) {
      setDetail(null);
      return;
    }
    setBusy(true);
    void (async () => {
      try {
        const result = await runtime.request("team/read", {
          projectId: project.id,
          entityId: selectedId,
        });
        setDetail(result);
      } catch (error) {
        setDetail(null);
        setStatus(String(error));
      } finally {
        setBusy(false);
      }
    })();
  }, [project, runtime, selectedId]);

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

  return (
    <section className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>{t("team.eyebrow", "Team context")}</p>
          <h2>{project.displayName}</h2>
          <p className={styles.hint}>
            {t(
              "team.readonlyHint",
              "Read-only in this phase: layers, knowledge, and the assembled context the agent sees. Editing and the product library follow in the next phase.",
            )}
          </p>
        </div>
        <button
          type="button"
          className={styles.iconButton}
          disabled={busy}
          onClick={() => void loadTree()}
          title={t("team.refresh", "Refresh")}
        >
          <RefreshCw size={16} />
        </button>
      </header>

      <div className={styles.body}>
        <aside className={styles.sidebar}>
          {tree?.bind ? (
            <p className={styles.bound}>
              <Layers size={14} />
              <code>{tree.bind}</code>
            </p>
          ) : (
            <p className={styles.empty}>
              {t(
                "team.unbound",
                "This workspace does not bind to a task yet.",
              )}
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

          {tree && tree.entities.length === 0 ? (
            <p className={styles.empty}>
              {t(
                "team.empty",
                "No layers configured yet. Add .deepcode/team/<layer>/<slug>/ in the workspace.",
              )}
            </p>
          ) : null}
        </aside>

        <main className={styles.editor}>
          {detail ? (
            <>
              <div className={styles.editorBar}>
                <code>{detail.entity.id}</code>
                <button type="button" disabled={busy} onClick={() => void showPreview()}>
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
                      </tr>
                    </thead>
                    <tbody>
                      {detail.knowledge.map((entry) => (
                        <tr key={entry.id}>
                          <td>{entry.type}</td>
                          <td>{entry.statement}</td>
                          <td>{entry.confidence}</td>
                          <td>{entry.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

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
