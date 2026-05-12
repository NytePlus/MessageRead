import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import QRCode from "react-qr-code";

const apiBase = (() => {
  const raw = import.meta.env.VITE_API_BASE;
  if (raw !== undefined && raw !== "") return raw.replace(/\/$/, "");
  return import.meta.env.DEV ? "https://localhost:4000" : "";
})();

type CreateResp = {
  id: string;
  openUrl: string;
  statusUrl: string;
};

type StatusResp = {
  read: boolean;
  readAt: number | null;
  createdAt: number;
};

export default function App() {
  const [toName, setToName] = useState("");
  const [body, setBody] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [result, setResult] = useState<CreateResp | null>(null);
  const [status, setStatus] = useState<StatusResp | null>(null);

  useEffect(() => {
    if (!result?.id) return;

    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${apiBase}/api/messages/${result.id}/status`);
        if (!r.ok) return;
        const j = (await r.json()) as StatusResp;
        if (!cancelled) setStatus(j);
      } catch {
        /* ignore poll errors */
      }
    };

    tick();
    const id = setInterval(tick, 2500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [result?.id]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    setResult(null);
    setStatus(null);
    try {
      const r = await fetch(`${apiBase}/api/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ toName: toName.trim(), body: body.trim() }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(typeof j.error === "string" ? j.error : "提交失败");
        return;
      }
      setResult(j as CreateResp);
    } catch {
      setError("无法连接后端，请确认 HTTPS 后端服务已启动（默认 https://localhost:4000）。");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="shell">
      <header className="header">
        <h1>消息已读统计</h1>
        <p className="sub">填写内容后生成链接与二维码；对方首次打开页面即记为已读。</p>
      </header>

      <form className="form" onSubmit={onSubmit}>
        <label className="field">
          <span className="label">TA 的名字</span>
          <input
            className="input"
            value={toName}
            onChange={(e) => setToName(e.target.value)}
            placeholder="例如：小明"
            autoComplete="off"
          />
        </label>
        <label className="field">
          <span className="label">你想对 TA 说的话</span>
          <textarea
            className="input textarea"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="写在这里…"
            rows={5}
          />
        </label>
        {error ? <p className="err">{error}</p> : null}
        <button className="btn" type="submit" disabled={pending}>
          {pending ? "提交中…" : "提交消息"}
        </button>
      </form>

      {result ? (
        <section className="out">
          <h2 className="out-title">分享链接</h2>
          <p className="link-wrap">
            <a className="link" href={result.openUrl} target="_blank" rel="noreferrer">
              {result.openUrl}
            </a>
          </p>
          <div className="qr-wrap">
            <QRCode value={result.openUrl} size={200} />
          </div>
          <div className="status">
            <span className="status-label">已读状态</span>
            {status?.read ? (
              <span className="badge read">
                已读
                {status.readAt != null
                  ? ` · ${new Date(status.readAt).toLocaleString()}`
                  : ""}
              </span>
            ) : (
              <span className="badge unread">未读</span>
            )}
          </div>
        </section>
      ) : null}

      <style>{`
        .shell {
          width: min(460px, 100%);
          background: rgba(15, 23, 42, 0.75);
          backdrop-filter: blur(12px);
          border: 1px solid rgba(148, 163, 184, 0.28);
          border-radius: 20px;
          padding: 28px 26px 32px;
          box-shadow: 0 28px 60px rgba(0, 0, 0, 0.45);
        }
        .header h1 {
          margin: 0 0 8px;
          font-size: 1.35rem;
          font-weight: 600;
          letter-spacing: 0.02em;
        }
        .sub {
          margin: 0;
          font-size: 0.875rem;
          color: #94a3b8;
          line-height: 1.5;
        }
        .form {
          margin-top: 22px;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .label {
          font-size: 0.8rem;
          color: #cbd5e1;
          font-weight: 500;
        }
        .input {
          width: 100%;
          padding: 10px 12px;
          border-radius: 10px;
          border: 1px solid rgba(148, 163, 184, 0.35);
          background: rgba(30, 41, 59, 0.6);
          color: #f1f5f9;
          font-size: 1rem;
          outline: none;
        }
        .input:focus {
          border-color: #38bdf8;
          box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.25);
        }
        .textarea {
          resize: vertical;
          min-height: 100px;
          line-height: 1.5;
        }
        .err {
          margin: 0;
          font-size: 0.875rem;
          color: #fca5a5;
        }
        .btn {
          margin-top: 4px;
          padding: 12px 16px;
          border: none;
          border-radius: 12px;
          background: linear-gradient(135deg, #0ea5e9, #2563eb);
          color: #fff;
          font-size: 1rem;
          font-weight: 600;
          cursor: pointer;
        }
        .btn:disabled {
          opacity: 0.65;
          cursor: not-allowed;
        }
        .out {
          margin-top: 26px;
          padding-top: 22px;
          border-top: 1px solid rgba(148, 163, 184, 0.2);
        }
        .out-title {
          margin: 0 0 10px;
          font-size: 1rem;
          font-weight: 600;
        }
        .link-wrap {
          margin: 0 0 16px;
          word-break: break-all;
        }
        .link {
          color: #7dd3fc;
          font-size: 0.85rem;
        }
        .qr-wrap {
          display: flex;
          justify-content: center;
          padding: 16px;
          background: #fff;
          border-radius: 14px;
          width: fit-content;
          margin: 0 auto 18px;
        }
        .status {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 10px;
          font-size: 0.9rem;
        }
        .status-label {
          color: #94a3b8;
        }
        .badge {
          padding: 4px 10px;
          border-radius: 999px;
          font-weight: 500;
        }
        .badge.unread {
          background: rgba(251, 191, 36, 0.18);
          color: #fcd34d;
        }
        .badge.read {
          background: rgba(74, 222, 128, 0.16);
          color: #86efac;
        }
      `}</style>
    </div>
  );
}
