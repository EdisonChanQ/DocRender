import { useEffect, useState } from "react";

import {
  fetchDatabaseConfig,
  fetchDatabaseStatus,
  fetchDatabaseTypes,
  saveDatabaseConfig,
  testDatabaseConnection,
} from "../api/database";
import type {
  DatabaseConfigPayload,
  DatabaseStatus,
  DatabaseTestResult,
  DatabaseType,
  DatabaseTypeOption,
} from "../types";

const DEFAULT_PAYLOAD: DatabaseConfigPayload = {
  type: "sqlite",
  host: "127.0.0.1",
  port: null,
  database: "storage/app.db",
  username: "",
  password: null,
  charset: "utf8mb4",
  driver: null,
};

function statusClass(status: DatabaseStatus | null): string {
  if (!status || !status.configured) return "status status--muted";
  return status.reachable ? "status status--ok" : "status status--error";
}

function statusText(status: DatabaseStatus | null): string {
  if (!status || !status.configured) return "未配置";
  return status.reachable ? "连接正常" : "连接异常";
}

export default function DatabasePage() {
  const [types, setTypes] = useState<DatabaseTypeOption[]>([]);
  const [form, setForm] = useState<DatabaseConfigPayload>(DEFAULT_PAYLOAD);
  const [status, setStatus] = useState<DatabaseStatus | null>(null);
  const [testResult, setTestResult] = useState<DatabaseTestResult | null>(null);
  const [passwordSet, setPasswordSet] = useState(false);
  const [configFile, setConfigFile] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [typeOptions, config, currentStatus] = await Promise.all([
          fetchDatabaseTypes(),
          fetchDatabaseConfig(),
          fetchDatabaseStatus(),
        ]);
        if (!active) return;
        setTypes(typeOptions);
        setPasswordSet(config.password_set);
        setConfigFile(config.config_file);
        setForm({
          type: config.type,
          host: config.host,
          port: config.port,
          database: config.database,
          username: config.username,
          password: null,
          charset: config.charset,
          driver: config.driver,
        });
        setStatus(currentStatus);
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "加载数据库配置失败");
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    return () => {
      active = false;
    };
  }, []);

  const isSqlite = form.type === "sqlite";
  const isMysql = form.type === "mysql";
  const isSqlServer = form.type === "sqlserver";

  function update<K extends keyof DatabaseConfigPayload>(
    key: K,
    value: DatabaseConfigPayload[K],
  ) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setNotice(null);
    setError(null);
  }

  function handleTypeChange(next: DatabaseType) {
    const option = types.find((item) => item.value === next) ?? null;
    setForm((prev) => ({
      ...prev,
      type: next,
      port: next === "sqlite" ? null : option?.default_port ?? null,
      driver: next === "sqlserver" ? option?.default_driver ?? null : null,
    }));
    setTestResult(null);
    setNotice(null);
    setError(null);
  }

  async function handleTest() {
    setTesting(true);
    setError(null);
    setNotice(null);
    try {
      setTestResult(await testDatabaseConnection(form));
    } catch (err) {
      setTestResult(null);
      setError(err instanceof Error ? err.message : "测试连接失败");
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await saveDatabaseConfig(form);
      setPasswordSet(saved.password_set);
      setForm((prev) => ({ ...prev, password: null }));
      setNotice("数据库配置已保存");
      setStatus(await fetchDatabaseStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="page">
        <header className="page__header">
          <h1>数据库配置</h1>
          <p>正在加载配置…</p>
        </header>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page__header">
        <h1>数据库配置</h1>
        <p>配置系统使用的数据库连接，保存后立即生效。</p>
        {configFile && <p className="page__hint">配置文件：{configFile}</p>}
      </header>

      <section className="panel panel--compact">
        <div className="panel__meta">
          <strong>连接状态</strong>
          <span className={statusClass(status)}>{statusText(status)}</span>
          {status?.configured && (
            <span className={`badge badge--${status.reachable ? "hybrid" : "ocr"}`}>
              {status.dialect ?? form.type}
            </span>
          )}
          {status?.server_version && <span>{status.server_version}</span>}
        </div>
        {status?.message && <p className="status__message">{status.message}</p>}
      </section>

      <section className="panel">
        <h2>连接参数</h2>
        <form
          className="form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!saving) void handleSave();
          }}
        >
          <div className="form__grid">
            <label className="form__field">
              <span>数据库类型</span>
              <select
                value={form.type}
                onChange={(event) => handleTypeChange(event.target.value as DatabaseType)}
              >
                {types.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="form__field">
              <span>数据库{isSqlite ? "文件路径" : "名称"}</span>
              <input
                value={form.database}
                placeholder={isSqlite ? "storage/app.db" : "mydb"}
                onChange={(event) => update("database", event.target.value)}
              />
            </label>

            <label className="form__field">
              <span>主机地址</span>
              <input
                value={form.host}
                disabled={isSqlite}
                placeholder="127.0.0.1"
                onChange={(event) => update("host", event.target.value)}
              />
            </label>

            <label className="form__field">
              <span>端口</span>
              <input
                type="number"
                value={form.port ?? ""}
                disabled={isSqlite}
                placeholder="默认端口"
                onChange={(event) =>
                  update("port", event.target.value === "" ? null : Number(event.target.value))
                }
              />
            </label>

            <label className="form__field">
              <span>用户名</span>
              <input
                value={form.username}
                disabled={isSqlite}
                placeholder="数据库用户名"
                onChange={(event) => update("username", event.target.value)}
              />
            </label>

            <label className="form__field">
              <span>密码</span>
              <input
                type="password"
                value={form.password ?? ""}
                disabled={isSqlite}
                placeholder={passwordSet ? "留空表示沿用已保存的密码" : "数据库密码"}
                onChange={(event) => update("password", event.target.value || null)}
              />
            </label>

            {isMysql && (
              <label className="form__field">
                <span>字符集</span>
                <input
                  value={form.charset}
                  placeholder="utf8mb4"
                  onChange={(event) => update("charset", event.target.value)}
                />
              </label>
            )}

            {isSqlServer && (
              <label className="form__field">
                <span>ODBC 驱动</span>
                <input
                  value={form.driver ?? ""}
                  placeholder="ODBC Driver 17 for SQL Server"
                  onChange={(event) => update("driver", event.target.value || null)}
                />
              </label>
            )}
          </div>

          {error && <p className="error">{error}</p>}
          {notice && <p className="notice">{notice}</p>}

          <div className="form__actions">
            <button type="button" onClick={handleTest} disabled={testing || saving}>
              {testing ? "测试中…" : "测试连接"}
            </button>
            <button type="submit" className="primary" disabled={testing || saving}>
              {saving ? "保存中…" : "保存配置"}
            </button>
          </div>
        </form>

        {testResult && (
          <div className={`result-banner result-banner--${testResult.success ? "ok" : "error"}`}>
            <strong>{testResult.success ? "连接成功" : "连接失败"}</strong>
            <span>{testResult.message}</span>
            {testResult.dialect && <span>方言：{testResult.dialect}</span>}
            {testResult.server_version && <span>版本：{testResult.server_version}</span>}
            {testResult.elapsed_ms != null && <span>耗时：{testResult.elapsed_ms} ms</span>}
          </div>
        )}
      </section>
    </div>
  );
}
