import { lazy, Suspense, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  File,
  FileCode2,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useSystemDarkMode } from "../../app/useSystemDarkMode";
import type { CodeWorkbenchController } from "../workbench/useCodeWorkbench";
import { InspectorEmpty } from "./InspectorEmpty";
import { languageFor } from "./inspectorFormat";
import styles from "./Inspector.module.css";

const LocalMonacoEditor = lazy(() => import("../workbench/LocalMonacoEditor"));

function fileIcon(name: string) {
  const lower = name.toLowerCase();
  if (lower.endsWith(".json") || lower.endsWith(".jsonc")) return FileJson;
  if (
    /\.(ts|tsx|js|jsx|mjs|cjs|py|rs|go|java|kt|cs|c|cpp|h|rb|php|swift|sh|ps1)$/.test(
      lower,
    )
  )
    return FileCode2;
  if (/\.(md|mdx|txt|rst|yaml|yml|toml|ini)$/.test(lower)) return FileText;
  return File;
}

interface FilesPanelProps {
  trusted: boolean;
  hasActiveTurn: boolean;
  workbench: CodeWorkbenchController;
  onOpenFile(path: string): Promise<void>;
  onDownload?: (path: string) => Promise<void>;
}

export function FilesPanel({
  trusted,
  hasActiveTurn,
  workbench,
  onOpenFile,
  onDownload,
}: FilesPanelProps) {
  const { t } = useTranslation();
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const darkMode = useSystemDarkMode();
  const dirty = Boolean(
    workbench.file && workbench.draft !== workbench.file.content,
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const rows = useMemo(() => {
    return workbench.entries.filter((entry) => {
      if (!entry.path) return false;
      const parts = entry.path.split("/");
      if (entry.hidden || parts.some((part) => part.startsWith("."))) {
        return false;
      }
      let prefix = "";
      for (let index = 0; index < parts.length - 1; index += 1) {
        prefix = prefix ? `${prefix}/${parts[index]}` : parts[index];
        if (!expanded.has(prefix)) return false;
      }
      return true;
    });
  }, [workbench.entries, expanded]);

  const toggle = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <div className={styles.filesView}>
      <div className={styles.fileTreeWrap}>
        <div className={styles.treeHeader}>
          <span className={styles.treeTitle}>
            {t("inspector.files.label", "资源管理器")}
          </span>
          <span className={styles.treeCount}>{workbench.entries.length}</span>
          <span className={styles.treeActions}>
            <button
              type="button"
              title="全部展开"
              aria-label="全部展开"
              onClick={() =>
                setExpanded(
                  new Set(
                    workbench.entries
                      .filter((entry) => entry.kind === "directory")
                      .map((entry) => entry.path),
                  ),
                )
              }
            >
              <ChevronsUpDown size={13} />
            </button>
            <button
              type="button"
              title="全部折叠"
              aria-label="全部折叠"
              onClick={() => setExpanded(new Set())}
            >
              <ChevronsDownUp size={13} />
            </button>
          </span>
        </div>
        <div
          className={styles.fileTree}
          aria-label={t("inspector.files.label", "Workspace files")}
        >
          {rows.map((entry) => {
            const isDirectory = entry.kind === "directory";
            const depth = entry.path.split("/").length - 1;
            const open = expanded.has(entry.path);
            const Icon = fileIcon(entry.name);
            return (
              <div
                key={entry.path}
                className={styles.treeRow}
                style={{ paddingLeft: `${6 + depth * 14}px` }}
              >
                {isDirectory ? (
                  <button
                    type="button"
                    className={styles.treeChevron}
                    aria-label={open ? "折叠" : "展开"}
                    onClick={() => toggle(entry.path)}
                  >
                    {open ? (
                      <ChevronDown size={13} />
                    ) : (
                      <ChevronRight size={13} />
                    )}
                  </button>
                ) : (
                  <span className={styles.treeSpacer} />
                )}
                <span className={styles.treeIcon} aria-hidden="true">
                  {isDirectory ? (
                    open ? (
                      <FolderOpen size={14} />
                    ) : (
                      <Folder size={14} />
                    )
                  ) : (
                    <Icon size={14} />
                  )}
                </span>
                <button
                  type="button"
                  className={styles.treeLabel}
                  title={entry.path}
                  onClick={() =>
                    isDirectory ? toggle(entry.path) : void onOpenFile(entry.path)
                  }
                >
                  {entry.name}
                </button>
              </div>
            );
          })}
        </div>
        {workbench.entriesTruncated ? (
          <p className={styles.treeNotice}>
            {t(
              "inspector.files.truncated",
              "File list limited to the first 750 entries.",
            )}
          </p>
        ) : null}
      </div>
      {workbench.file ? (
        <div className={styles.fileEditor}>
          <header>
            <span title={workbench.file.path}>{workbench.file.path}</span>
            <div>
              {onDownload && <button type="button" onClick={() => {
                setDownloadError(null);
                void onDownload(workbench.file!.path).catch((error) => setDownloadError(String(error)));
              }}>{t("inspector.files.download", "Download")}</button>}
              {workbench.file.truncated ? (
                <small>
                  {t("inspector.files.truncatedReadOnly", "Truncated · read-only")}
                </small>
              ) : dirty ? (
                <small>{t("inspector.files.unsaved", "Unsaved")}</small>
              ) : null}
              <button
                type="button"
                onClick={() => void workbench.saveFile()}
                disabled={
                  !trusted ||
                  hasActiveTurn ||
                  workbench.file.truncated ||
                  !dirty ||
                  workbench.loading
                }
              >
                {t("inspector.files.save", "Save")}
              </button>
            </div>
          </header>
          {downloadError && <p role="alert">{downloadError}</p>}
          <Suspense
            fallback={
              <div className={styles.editorLoading}>
                {t("inspector.files.loadingEditor", "Loading editor…")}
              </div>
            }
          >
            <LocalMonacoEditor
              height="100%"
              language={languageFor(workbench.file.path)}
              value={workbench.draft}
              onChange={(value) => workbench.setDraft(value ?? "")}
              theme={darkMode ? "vs-dark" : "vs-light"}
              options={{
                minimap: { enabled: false },
                readOnly:
                  !trusted || hasActiveTurn || workbench.file.truncated,
                fontSize: 12,
                lineHeight: 20,
                renderLineHighlight: "line",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                automaticLayout: true,
              }}
            />
          </Suspense>
          {hasActiveTurn ? (
            <p className={styles.editorNotice}>
              {t(
                "inspector.files.locked",
                "Editing is locked until the active Turn finishes.",
              )}
            </p>
          ) : null}
        </div>
      ) : (
        <InspectorEmpty
          label={t("inspector.files.empty", "Select a text file to inspect it.")}
        />
      )}
    </div>
  );
}
