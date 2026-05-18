import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import QRCode from "react-qr-code";

/** IPv4 地址（hostname 部分） */
function isIPv4Hostname(hostname: string): boolean {
  return /^(\d{1,3}\.){3}\d{1,3}$/.test(hostname);
}

/**
 * 按约定选择协议：IP:端口 / 本机 → http；域名 → https（避免 HTTPS 页面混合内容）。
 * 显式传入的协议会被覆盖为上述规则。
 */
function useHttpSchemeForApi(hostname: string): boolean {
  const h = hostname.toLowerCase();
  if (h === "localhost" || h === "127.0.0.1" || h === "::1") {
    return true;
  }
  if (h.startsWith("[") && h.endsWith("]")) {
    return true;
  }
  if (isIPv4Hostname(h)) {
    return true;
  }
  if (h.includes(":")) {
    return true;
  }
  return false;
}

function normalizeApiBase(raw: string): string {
  const trimmed = raw.trim().replace(/\/$/, "");
  if (trimmed === "") {
    return "";
  }

  try {
    let url: URL;
    if (/^https?:\/\//i.test(trimmed)) {
      url = new URL(trimmed);
    } else if (trimmed.startsWith("//")) {
      url = new URL(`http:${trimmed}`);
    } else {
      url = new URL(`http://${trimmed}`);
    }

    url.protocol = useHttpSchemeForApi(url.hostname) ? "http:" : "https:";

    let out = url.toString();
    out = out.replace(/\/+$/, "");
    return out || trimmed;
  } catch {
    return trimmed;
  }
}

const apiBase = (() => {
  const raw = import.meta.env.VITE_API_BASE;
  if (raw !== undefined && raw !== "") {
    return normalizeApiBase(raw);
  }
  return import.meta.env.DEV ? "http://localhost:4000" : ".";
})();

type Pricing = {
  id: number;
  price: string;
  createdAt: number;
};

type Provider = {
  id: number;
  uuid: string;
  createdAt: number;
  authorizedAt: number | null;
  status: string;
  authAppId: string;
  userId: string;
  hasToken: boolean;
};

type PayConfig = {
  providerExists: boolean;
  providerUuid: string;
  pricing: Pricing | null;
  paid: boolean;
};

type OrderStatus = {
  outTradeNo: string;
  status: string;
  paid: boolean;
};

type AuthUrlResp = {
  provider: Provider;
  authUrl: string;
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data.error === "string" ? data.error : "请求失败");
  }
  return data as T;
}

function fmtTime(ms: number | null) {
  if (!ms) return "-";
  return new Date(ms).toLocaleString();
}

const PENDING_AUTH_STORAGE = "alipayPendingAuth:";

export default function App() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const isAdmin = params.get("admin") === "1";
  const authQrUuid = params.get("authQr")?.trim() || "";
  const providerUuid = params.get("provider") || "";
  const orderNo = params.get("order") || "";

  if (isAdmin && authQrUuid) {
    return <AdminAuthQrPage providerUuid={authQrUuid} />;
  }
  return isAdmin ? <AdminPage /> : <PayPage providerUuid={providerUuid} orderNo={orderNo} />;
}

function AdminPage() {
  const [password, setPassword] = useState("");
  const [authed, setAuthed] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [priceInput, setPriceInput] = useState("");
  /** 待授权服务商对应的授权链接（授权成功后由服务端状态与本地清理同步移除） */
  const [pendingAuthByUuid, setPendingAuthByUuid] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function syncPendingAuthFromServer(list: Provider[]) {
    const next: Record<string, string> = {};
    for (const p of list) {
      if (p.status !== "pending") {
        sessionStorage.removeItem(PENDING_AUTH_STORAGE + p.uuid);
        continue;
      }
      const stored = sessionStorage.getItem(PENDING_AUTH_STORAGE + p.uuid);
      if (stored) {
        next[p.uuid] = stored;
      }
    }
    setPendingAuthByUuid(next);
  }

  async function loadAdminData() {
    const [providersResp, pricingResp] = await Promise.all([
      api<{ providers: Provider[] }>("/api/admin/providers"),
      api<{ pricing: Pricing | null }>("/api/admin/pricing"),
    ]);
    setProviders(providersResp.providers);
    setPricing(pricingResp.pricing);
    setPriceInput(pricingResp.pricing?.price || "");
    syncPendingAuthFromServer(providersResp.providers);
  }

  useEffect(() => {
    api<{ authenticated: boolean }>("/api/admin/me")
      .then((res) => {
        setAuthed(res.authenticated);
        if (res.authenticated) return loadAdminData();
      })
      .catch(() => undefined);
  }, []);

  async function login(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      setAuthed(true);
      await loadAdminData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  async function createAuthUrl() {
    setError("");
    setLoading(true);
    try {
      const res = await api<AuthUrlResp>("/api/admin/providers/auth-url", { method: "POST" });
      sessionStorage.setItem(PENDING_AUTH_STORAGE + res.provider.uuid, res.authUrl);
      setPendingAuthByUuid((prev) => ({ ...prev, [res.provider.uuid]: res.authUrl }));
      await loadAdminData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建授权链接失败");
    } finally {
      setLoading(false);
    }
  }

  async function savePrice(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await api<{ pricing: Pricing }>("/api/admin/pricing", {
        method: "POST",
        body: JSON.stringify({ price: priceInput }),
      });
      setPricing(res.pricing);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存收费标准失败");
    } finally {
      setLoading(false);
    }
  }

  if (!authed) {
    return (
      <Page title="管理员登录" subtitle="输入管理员密码后管理服务商授权和收费标准">
        <form className="card stack" onSubmit={login}>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="管理员密码" />
          {error ? <p className="error">{error}</p> : null}
          <button className="btn" disabled={loading || !password}>登录</button>
        </form>
      </Page>
    );
  }

  return (
    <Page title="服务商管理" subtitle="生成授权二维码，授权成功后凭证会永久保存到 Redis">
      <section className="card stack">
        <h2>收费标准</h2>
        <p className="muted">当前价位：{pricing ? `¥ ${pricing.price}` : "未配置"}</p>
        <form className="row" onSubmit={savePrice}>
          <input className="input" value={priceInput} onChange={(e) => setPriceInput(e.target.value)} placeholder="例如 9.90" />
          <button className="btn" disabled={loading || !priceInput}>保存</button>
        </form>
      </section>

      <section className="card stack">
        <div className="row between">
          <h2>服务商列表</h2>
          <button className="btn" onClick={createAuthUrl} disabled={loading}>添加服务商</button>
        </div>
        <p className="muted small-hint">添加后请在对应「待授权」行的「授权二维码页」扫码；授权成功后该行不再显示二维码入口。</p>
        {error ? <p className="error">{error}</p> : null}
        <div className="list">
          {providers.map((provider) => (
            <div className="item" key={provider.uuid}>
              <div>
                <strong>#{provider.id} {provider.uuid}</strong>
                <p className="muted">状态：{provider.status} · 授权时间：{fmtTime(provider.authorizedAt)}</p>
                <p className="muted">userId：{provider.userId || "-"} · authAppId：{provider.authAppId || "-"}</p>
              </div>
              <div className="item-links">
                <a href={`?provider=${provider.uuid}`} target="_blank" rel="noreferrer">用户收费页</a>
                {provider.status === "pending" && pendingAuthByUuid[provider.uuid] ? (
                  <a href={`?admin=1&authQr=${encodeURIComponent(provider.uuid)}`} target="_blank" rel="noreferrer">
                    授权二维码页
                  </a>
                ) : null}
              </div>
            </div>
          ))}
          {providers.length === 0 ? <p className="muted">暂无服务商</p> : null}
        </div>
      </section>
      <Styles />
    </Page>
  );
}

function AdminAuthQrPage({ providerUuid }: { providerUuid: string }) {
  const authUrl = sessionStorage.getItem(PENDING_AUTH_STORAGE + providerUuid) || "";

  return (
    <Page title="服务商授权" subtitle={`UUID：${providerUuid}`}>
      <section className="card stack center">
        {!authUrl ? (
          <>
            <p className="error">未找到该服务商的授权链接，请返回列表重新「添加服务商」或确认本浏览器未清除站点数据。</p>
            <a className="btn" href="?admin=1">返回管理页</a>
          </>
        ) : (
          <>
            <p className="muted">请使用支付宝扫描下方二维码完成授权。授权完成后可关闭本页。</p>
            <div className="auth-box">
              <div className="qr">
                <QRCode value={authUrl} size={220} />
              </div>
            </div>
            <div className="row center-wrap">
              <a className="btn link-btn" href={authUrl} target="_blank" rel="noreferrer">
                打开授权链接
              </a>
              <a className="btn btn-outline" href="?admin=1">
                返回管理页
              </a>
            </div>
          </>
        )}
      </section>
      <Styles />
    </Page>
  );
}

function PayPage({ providerUuid, orderNo }: { providerUuid: string; orderNo: string }) {
  const [config, setConfig] = useState<PayConfig | null>(null);
  const [order, setOrder] = useState<OrderStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function loadConfig() {
    if (!providerUuid) return;
    const res = await api<PayConfig>(`/api/pay/config?providerUuid=${encodeURIComponent(providerUuid)}`);
    setConfig(res);
  }

  useEffect(() => {
    loadConfig().catch((err) => setError(err instanceof Error ? err.message : "加载收费信息失败"));
  }, [providerUuid]);

  useEffect(() => {
    if (!orderNo) return;
    const tick = () => {
      api<OrderStatus>(`/api/pay/orders?outTradeNo=${encodeURIComponent(orderNo)}`)
        .then((res) => {
          setOrder(res);
          if (res.paid) loadConfig().catch(() => undefined);
        })
        .catch(() => undefined);
    };
    tick();
    const id = window.setInterval(tick, 2500);
    return () => window.clearInterval(id);
  }, [orderNo]);

  async function startPay() {
    setError("");
    setLoading(true);
    try {
      const res = await api<{ payUrl?: string; paid?: boolean; outTradeNo?: string }>("/api/pay/orders", {
        method: "POST",
        body: JSON.stringify({ providerUuid }),
      });
      if (res.paid) {
        await loadConfig();
      } else if (res.payUrl) {
        window.location.href = res.payUrl;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建支付订单失败");
    } finally {
      setLoading(false);
    }
  }

  if (!providerUuid) {
    return (
      <Page title="收费下载" subtitle="缺少服务商 UUID">
        <section className="card"><p className="error">请通过服务商专属链接进入收费页面。</p></section>
      </Page>
    );
  }

  const paid = config?.paid || order?.paid;
  const canPay = config?.providerExists && config?.pricing;

  return (
    <Page title="收费下载" subtitle={`服务商：${providerUuid}`}>
      <section className="card stack center">
        {error ? <p className="error">{error}</p> : null}
        {!config ? <p className="muted">正在加载收费信息...</p> : null}
        {config && !config.providerExists ? <p className="error">服务商不存在或尚未完成授权。</p> : null}
        {config?.providerExists && !config.pricing ? <p className="error">管理员尚未配置收费标准。</p> : null}
        {canPay ? (
          <>
            <p className="price">¥ {config.pricing?.price}</p>
            <p className="muted">支付成功后可下载 APK。请及时下载，在当前浏览器 session 保存期间无需再次付费。</p>
            {orderNo && !paid ? <p className="muted">支付结果确认中，请稍候...</p> : null}
            {paid ? (
              <a className="btn link-btn" href={`${apiBase}/api/download/apk?providerUuid=${encodeURIComponent(providerUuid)}`}>下载 APK</a>
            ) : (
              <button className="btn" onClick={startPay} disabled={loading}>跳转支付宝付款</button>
            )}
          </>
        ) : null}
      </section>
      <Styles />
    </Page>
  );
}

function Page({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <main className="shell">
      <header className="hero">
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </header>
      {children}
      <Styles />
    </main>
  );
}

function Styles() {
  return (
    <style>{`
      .shell { width: min(960px, calc(100% - 32px)); margin: 40px auto; color: #172033; }
      .hero { margin-bottom: 22px; padding-bottom: 18px; border-bottom: 1px solid #e5eaf2; }
      .hero h1 { margin: 0 0 8px; font-size: 30px; letter-spacing: -.02em; color: #0f172a; }
      .hero p, .muted { color: #64748b; line-height: 1.6; }
      .card { background: #fff; border: 1px solid #e5eaf2; border-radius: 16px; padding: 24px; margin-bottom: 16px; box-shadow: 0 12px 32px rgba(15, 23, 42, .08); }
      .stack { display: flex; flex-direction: column; gap: 14px; }
      .row { display: flex; gap: 12px; align-items: center; }
      .between { justify-content: space-between; }
      .center { align-items: center; text-align: center; }
      h2 { margin: 0; font-size: 19px; color: #0f172a; }
      p { margin: 0; }
      .input { flex: 1; min-width: 0; border: 1px solid #cbd5e1; border-radius: 10px; padding: 12px 14px; background: #fff; color: #0f172a; font-size: 16px; outline: none; }
      .input:focus { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }
      .btn { border: none; border-radius: 10px; padding: 12px 18px; background: #1d4ed8; color: white; font-weight: 700; cursor: pointer; text-decoration: none; display: inline-block; box-shadow: 0 8px 18px rgba(29, 78, 216, .2); }
      .btn:disabled { opacity: .55; cursor: not-allowed; }
      .link-btn { background: #0f766e; box-shadow: 0 8px 18px rgba(15, 118, 110, .18); }
      .error { color: #dc2626; background: #fef2f2; border: 1px solid #fecaca; border-radius: 10px; padding: 10px 12px; }
      .price { font-size: 46px; font-weight: 800; color: #0f172a; }
      .auth-box { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
      .qr { background: white; border: 1px solid #e5eaf2; border-radius: 14px; padding: 14px; width: fit-content; }
      .list { display: flex; flex-direction: column; gap: 10px; }
      .item { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px; border: 1px solid #e5eaf2; border-radius: 14px; background: #f8fafc; }
      .item-links { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; flex-shrink: 0; }
      .small-hint { font-size: 0.85rem; margin: -4px 0 0; }
      .center-wrap { flex-wrap: wrap; justify-content: center; }
      .btn-outline { background: #fff; color: #1d4ed8; border: 1px solid #c7d2fe; box-shadow: none; }
      a { color: #1d4ed8; font-weight: 600; }
      @media (max-width: 640px) { .row, .item { flex-direction: column; align-items: stretch; } .item-links { align-items: flex-start; } .shell { margin-top: 20px; } }
    `}</style>
  );
}
