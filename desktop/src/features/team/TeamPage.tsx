import { useCallback, useEffect, useState } from "react";
import { FilePlus2, FolderTree, RefreshCw, Save } from "lucide-react";
import { useTranslation } from "react-i18next";

import type {
  FileContent,
  FileEntry,
  Project,
} from "../../generated/app-server";
import type { ClientRuntime } from "../../rpc/contracts";

import styles from "./TeamPage.module.css";

const LAYERS = [
  "company",
  "line",
  "product",
  "project",
  "batch",
  "version",
  "task",
] as const;

const TEMPLATES: Record<string, string> = {
  "entity.yaml":
    "# name defaults to the directory name; parent makes the chain.\nname: \nparent: \n",
  "knowledge.yaml": "[]\n",
  "AGENTS.md": "",
};

function templateFor(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? "";
  return TEMPLATES[name] ?? "";
}

interface TeamPageProps {
  runtime: ClientRuntime;
  project: Project | null;
}

export function TeamPage({ runtime, project }: TeamPageProps) {
  const { t } = useTranslation();
  const [threadId, setThreadId] = useState<string | null>(null);
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [selected, setSelected] = useState<FileContent | null>(null);
  const [draft, setDraft] = useState("");
  const [newPath, setNewPath] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadTree = useCallback(
    async (id: string) => {
      try {
        const result = await runtime.request("file/list", {
          threadId: id,
          path: ".deepcode/team",
          depth: 4,
          limit: 750,
        });
        setEntries(
          result.entries.filter((entry) => entry.kind === "file"),
        );
      } catch {
        setEntries([]);
      }
    },
    [runtime],
  );

  useEffect(() => {
    let cancelled = false;
    setStatus(null);
    setSelected(null);
    setEntries([]);
    if (!project) {
      setThreadId(null);
      return;
    }
    void (async () => {
      try {
        const list = await runtime.request("thread/list", {
          projectId: project.id,
          limit: 1,
        });
        if (cancelled) return;
        const id = list.threads[0]?.id ?? null;
        setThreadId(id);
        if (id) {
          await loadTree(id);
        } else {
          setStatus(
            t(
              "team.needSession",
              "Create one Session in this project first; team files are edited through a Session's workspace.",
            ),
          );
        }
      } catch (error) {
        if (!cancelled) setStatus(String(error));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadTree, project, runtime, t]);

  const open = async (path: string) => {
    if (!threadId) return;
    setBusy(true);
    try {
      const result = await runtime.request("file/read", {
        threadId,
        path,
      });
      setSelected(result.file);
      setDraft(result.file.content);
      setStatus(null);
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!threadId || !selected) return;
    setBusy(true);
    try {
      const result = await runtime.request("file/write", {
        threadId,
        path: selected.path,
        content: draft,
        expectedSha256: selected.sha256 ?? null,
      });
      setSelected(result.file);
      setDraft(result.file.content);
      setStatus(t("team.saved", "Saved"));
      await loadTree(threadId);
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    const path = newPath.trim().replaceAll("\\", "/");
    if (!threadId || !path) return;
    setBusy(true);
    try {
      const result = await runtime.request("file/write", {
        threadId,
        path: `.deepcode/team/${path}`,
        content: templateFor(path),
        expectedSha256: null,
      });
      setNewPath("");
      setSelected(result.file);
      setDraft(result.file.content);
      setStatus(t("team.created", "Created"));
      await loadTree(threadId);
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>
            {t("team.eyebrow", "Team context")}
          </p>
          <h2>{t("team.title", "Six-layer context")}</h2>
          <p className={styles.hint}>
            {t(
              "team.hint",
              "Every layer is a directory: entity.yaml (name, parent, external, references, designs, codebases, archived), knowledge.yaml, AGENTS.md. Layers are optional.",
            )}
          </p>
        </div>
        <button
          type="button"
          className={styles.iconButton}
          disabled={!threadId || busy}
          onClick={() => threadId && void loadTree(threadId)}
          title={t("team.refresh", "Refresh")}
        >
          <RefreshCw size={16} />
        </button>
      </header>

      <div className={styles.body}>
        <aside className={styles.sidebar}>
          <div className={styles.layers}>
            {LAYERS.map((layer) => (
              <span key={layer} className={styles.layer}>
                {layer}
              </span>
            ))}
          </div>

          <button
            type="button"
            className={styles.treeTitle}
            onClick={() => threadId && void loadTree(threadId)}
          >
            <FolderTree size={14} />
            {t("team.files", ".deepcode/team")}
          </button>

          {entries.length === 0 ? (
            <p className={styles.empty}>
              {t("team.empty", "No layer files yet.")}
            </p>
          ) : (
            <ul className={styles.files}>
              {entries.map((entry) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    data-active={selected?.path === entry.path}
                    onClick={() => void open(entry.path)}
                  >
                    <span>{entry.path.replace(".deepcode/team/", "")}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className={styles.newFile}>
            <input
              value={newPath}
              placeholder="task/LEAD-1/entity.yaml"
              onChange={(event) => setNewPath(event.target.value)}
              spellCheck={false}
            />
            <button
              type="button"
              disabled={!threadId || busy || !newPath.trim()}
              onClick={() => void create()}
              title={t("team.newFile", "New file")}
            >
              <FilePlus2 size={14} />
              {t("team.create", "Create")}
            </button>
          </div>
        </aside>

        <main className={styles.editor}>
          {selected ? (
            <>
              <div className={styles.editorBar}>
                <code>{selected.path.replace(".deepcode/team/", "")}</code>
                <button
                  type="button"
                  disabled={busy || draft === selected.content}
                  onClick={() => void save()}
                >
                  <Save size={14} />
                  {t("team.save", "Save")}
                </button>
              </div>
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                spellCheck={false}
                aria-label={selected.path}
              />
            </>
          ) : (
            <p className={styles.placeholder}>
              {t(
                "team.select",
                "Select a layer file to edit, or create one on the left.",
              )}
            </p>
          )}
          {status ? <p className={styles.status}>{status}</p> : null}
        </main>
      </div>
    </section>
  );
}
