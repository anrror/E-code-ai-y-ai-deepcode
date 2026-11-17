import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { ClientRuntime } from "../../rpc/contracts";
import styles from "./TeamPage.module.css";

interface DeliveryPanelProps {
  runtime: ClientRuntime;
  entityId: string;
}

interface JenkinsForm {
  url: string;
  job: string;
  user: string;
  token: string;
}

interface K8sForm {
  kubeconfig: string;
  context: string;
  namespace: string;
  deployment: string;
  labelSelector: string;
}

const EMPTY_JENKINS: JenkinsForm = { url: "", job: "", user: "", token: "" };
const EMPTY_K8S: K8sForm = {
  kubeconfig: "",
  context: "",
  namespace: "",
  deployment: "",
  labelSelector: "",
};

export function DeliveryPanel({ runtime, entityId }: DeliveryPanelProps) {
  const { t } = useTranslation();
  const [jenkins, setJenkins] = useState<JenkinsForm>(EMPTY_JENKINS);
  const [k8s, setK8s] = useState<K8sForm>(EMPTY_K8S);
  const [hasToken, setHasToken] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [consoleText, setConsoleText] = useState<string>("");
  const [pods, setPods] = useState<Array<Record<string, unknown>>>([]);
  const [logs, setLogs] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void runtime
      .request("integrations/read", { entityId })
      .then((result) => {
        if (cancelled || !result) {
          return;
        }
        const j = (result.jenkins ?? {}) as Record<string, unknown>;
        const k = (result.k8s ?? {}) as Record<string, unknown>;
        setJenkins({
          url: String(j.url ?? ""),
          job: String(j.job ?? ""),
          user: String(j.user ?? ""),
          token: "",
        });
        setHasToken(Boolean(j.hasToken));
        setK8s({
          kubeconfig: String(k.kubeconfig ?? ""),
          context: String(k.context ?? ""),
          namespace: String(k.namespace ?? ""),
          deployment: String(k.deployment ?? ""),
          labelSelector: String(k.labelSelector ?? ""),
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [entityId, runtime]);

  const guard = async (label: string, run: () => Promise<unknown>) => {
    setBusy(true);
    setMessage("");
    try {
      await run();
    } catch (caught) {
      setMessage(`${label}: ${String(caught)}`);
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    guard(t("team.delivery.save", "保存交付配置"), async () => {
      const result = await runtime.request("integrations/save", {
        entityId,
        jenkins: { ...jenkins } as unknown as Record<string, unknown>,
        k8s: { ...k8s } as unknown as Record<string, unknown>,
      });
      const savedJenkins = (result?.jenkins ?? {}) as Record<string, unknown>;
      setHasToken(Boolean(savedJenkins.hasToken));
      setMessage(t("team.delivery.saved", "已保存"));
    });

  const loadStatus = () =>
    guard(t("team.delivery.status", "Jenkins 状态"), async () => {
      const result = await runtime.request("jenkins/status", { entityId });
      const last = result?.lastBuild as Record<string, unknown> | null;
      setStatus(
        `#${last?.number ?? "-"} ${last?.result ?? (result?.building ? "BUILDING" : "?")}`,
      );
    });

  const publish = () =>
    guard(t("team.delivery.publish", "触发发布"), async () => {
      const result = await runtime.request("jenkins/trigger", { entityId });
      setMessage(
        `${t("team.delivery.queued", "已进入队列")} ${String(result?.queueUrl ?? "")}`,
      );
    });

  const showConsole = () =>
    guard(t("team.delivery.console", "构建日志"), async () => {
      const result = await runtime.request("jenkins/console", { entityId });
      setConsoleText(String(result?.text ?? ""));
    });

  const loadPods = () =>
    guard(t("team.delivery.pods", "Pod 列表"), async () => {
      const result = await runtime.request("k8s/pods", { entityId });
      setPods((result?.pods as Array<Record<string, unknown>>) ?? []);
    });

  const openLogs = (pod: string) =>
    guard(t("team.delivery.logs", "Pod 日志"), async () => {
      const result = await runtime.request("k8s/logs", { entityId, pod, tail: 300 });
      setLogs(String(result?.text ?? ""));
    });

  const restart = () =>
    guard(t("team.delivery.restart", "重启部署"), async () => {
      await runtime.request("k8s/restart", { entityId });
      setMessage(t("team.delivery.restarted", "已触发滚动重启"));
      await loadPods();
    });

  return (
    <section className={styles.delivery}>
      <h4>{t("team.delivery.title", "交付（Jenkins / Kubernetes）")}</h4>
      <p className={styles.muted}>
        {t(
          "team.delivery.hint",
          "只保存在本机 integrations.yaml，不进入团队快照，也不会发送给模型。",
        )}
      </p>

      <div className={styles.deliveryGrid}>
        <label>
          Jenkins URL
          <input
            value={jenkins.url}
            placeholder="https://jenkins.example.com"
            onChange={(event) => setJenkins({ ...jenkins, url: event.target.value })}
          />
        </label>
        <label>
          Job
          <input
            value={jenkins.job}
            placeholder="folder/job-name"
            onChange={(event) => setJenkins({ ...jenkins, job: event.target.value })}
          />
        </label>
        <label>
          用户
          <input
            value={jenkins.user}
            onChange={(event) => setJenkins({ ...jenkins, user: event.target.value })}
          />
        </label>
        <label>
          Token
          <input
            type="password"
            value={jenkins.token}
            placeholder={hasToken ? "已保存（留空不变）" : ""}
            onChange={(event) => setJenkins({ ...jenkins, token: event.target.value })}
          />
        </label>
      </div>
      <div className={styles.deliveryActions}>
        <button type="button" className={styles.ghost} disabled={busy} onClick={() => void loadStatus()}>
          状态
        </button>
        <button type="button" className={styles.primary} disabled={busy} onClick={() => void publish()}>
          发布
        </button>
        <button type="button" className={styles.ghost} disabled={busy} onClick={() => void showConsole()}>
          构建日志
        </button>
        {status ? <code className={styles.deliveryStatus}>{status}</code> : null}
      </div>
      {consoleText ? <pre className={styles.deliveryLog}>{consoleText}</pre> : null}

      <div className={styles.deliveryGrid}>
        <label>
          kubeconfig
          <input
            value={k8s.kubeconfig}
            placeholder="~/.kube/config"
            onChange={(event) => setK8s({ ...k8s, kubeconfig: event.target.value })}
          />
        </label>
        <label>
          Context
          <input
            value={k8s.context}
            onChange={(event) => setK8s({ ...k8s, context: event.target.value })}
          />
        </label>
        <label>
          Namespace
          <input
            value={k8s.namespace}
            placeholder="default"
            onChange={(event) => setK8s({ ...k8s, namespace: event.target.value })}
          />
        </label>
        <label>
          Deployment
          <input
            value={k8s.deployment}
            onChange={(event) => setK8s({ ...k8s, deployment: event.target.value })}
          />
        </label>
        <label>
          Label selector
          <input
            value={k8s.labelSelector}
            placeholder="app=api"
            onChange={(event) => setK8s({ ...k8s, labelSelector: event.target.value })}
          />
        </label>
      </div>
      <div className={styles.deliveryActions}>
        <button type="button" className={styles.ghost} disabled={busy} onClick={() => void loadPods()}>
          Pod 列表
        </button>
        <button type="button" className={styles.ghost} disabled={busy} onClick={() => void restart()}>
          重启部署
        </button>
        <button type="button" className={styles.primary} disabled={busy} onClick={() => void save()}>
          保存交付配置
        </button>
      </div>
      {pods.length > 0 ? (
        <div className={styles.podList}>
          {pods.map((pod) => (
            <button
              key={String(pod.name)}
              type="button"
              className={styles.podRow}
              onClick={() => void openLogs(String(pod.name))}
            >
              <code>{String(pod.name)}</code>
              <span>
                {String(pod.phase)} · ready {String(pod.ready)} · restarts{" "}
                {String(pod.restarts)}
              </span>
            </button>
          ))}
        </div>
      ) : null}
      {logs ? <pre className={styles.deliveryLog}>{logs}</pre> : null}
      {message ? <p className={styles.muted}>{message}</p> : null}
    </section>
  );
}
