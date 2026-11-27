import { FormEvent, useEffect, useRef, useState } from "react";
import {
  AnalyticsRow, ApiError, Campaign, CampaignSummary, Clip, Job, Publication, Settings, Source,
  api, sendJson,
} from "./api";

type Notify = (text: string, error?: boolean) => void;
type Page = "campaigns" | "sources" | "queue" | "publish" | "analytics" | "settings";

const PAGES: [Page, string][] = [
  ["campaigns", "Кампании"],
  ["sources", "Исходники"],
  ["queue", "Очередь клипов"],
  ["publish", "Публикация"],
  ["analytics", "Аналитика"],
  ["settings", "Настройки"],
];

// Loads `path` whenever it, `reload` or `version` changes; version bumps when a background job finishes.
function useLoad<T>(path: string | null, notify: Notify, version: number) {
  const [data, setData] = useState<T | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) {
      setData(null);
      return;
    }
    let live = true;
    api<T>(path)
      .then((d) => live && setData(d))
      .catch((e: Error) => live && notify(e.message, true));
    return () => {
      live = false;
    };
    // notify is recreated on every render; it is not a reason to reload
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, version]);
  return { data, reload: () => setTick((t) => t + 1) };
}

export function App() {
  const [page, setPage] = useState<Page>("campaigns");
  const [cid, setCid] = useState("");
  const [campaigns, setCampaigns] = useState<CampaignSummary[]>([]);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [version, setVersion] = useState(0);
  const [campaignsTick, setCampaignsTick] = useState(0);
  const finishedKey = useRef("");

  const notify: Notify = (text, error = false) => setNotice({ text, error });
  const reloadCampaigns = () => setCampaignsTick((t) => t + 1);

  useEffect(() => {
    api<CampaignSummary[]>("/api/campaigns")
      .then((list) => {
        setCampaigns(list);
        setCid((current) => current || list.find((c) => !c.error)?.id || "");
      })
      .catch((e: Error) => notify(e.message, true));
  }, [campaignsTick]);

  // Server-sent events: the job list is pushed on every change; a finished job triggers page reloads.
  useEffect(() => {
    const es = new EventSource("/api/events");
    es.onmessage = (ev) => {
      const list = JSON.parse(ev.data) as Job[];
      setJobs(list);
      const key = list.filter((j) => j.status === "done" || j.status === "failed").map((j) => j.id).join(",");
      if (finishedKey.current && key !== finishedKey.current) setVersion((v) => v + 1);
      finishedKey.current = key;
    };
    return () => es.close();
  }, []);

  const props = { cid, notify, version };
  return (
    <>
      <header>
        <h1>Clipper</h1>
        <nav>
          {PAGES.map(([id, label]) => (
            <button key={id} className={page === id ? "active" : ""} onClick={() => setPage(id)}>
              {label}
            </button>
          ))}
        </nav>
        <label>
          Кампания{" "}
          <select value={cid} onChange={(e) => setCid(e.target.value)}>
            <option value="">—</option>
            {campaigns.filter((c) => !c.error).map((c) => (
              <option key={c.id} value={c.id}>{c.name ?? c.id}</option>
            ))}
          </select>
        </label>
      </header>
      <main>
        {notice && (
          <div className={notice.error ? "notice error" : "notice"} onClick={() => setNotice(null)}>
            {notice.text}
          </div>
        )}
        {page === "campaigns" && <CampaignsPage campaigns={campaigns} onSaved={reloadCampaigns} setCid={setCid} {...props} />}
        {page === "sources" && <SourcesPage {...props} />}
        {page === "queue" && <QueuePage {...props} />}
        {page === "publish" && <PublishPage {...props} />}
        {page === "analytics" && <AnalyticsPage {...props} />}
        {page === "settings" && <SettingsPage notify={notify} />}
      </main>
      <JobPanel jobs={jobs} />
    </>
  );
}

function JobPanel({ jobs }: { jobs: Job[] }) {
  return (
    <footer>
      <strong>Задачи</strong>
      {jobs.length === 0 && <span className="muted"> пока нет</span>}
      {jobs.slice(0, 5).map((j) => (
        <div key={j.id} className={`job ${j.status}`}>
          #{j.id} {j.kind} · {j.campaign} · <b>{j.status}</b>
          <pre>{j.log.trim().split("\n").slice(-3).join("\n")}</pre>
        </div>
      ))}
    </footer>
  );
}

async function errorText(e: unknown) {
  return e instanceof ApiError ? e.message : String(e);
}

// ---------- Campaigns --------------------------------------------------------

interface CampaignForm {
  id: string; name: string; language: string; mode: Campaign["mode"]; sources: string;
  hashtags: string; mentions: string; accounts: string; logo_path: string; logo_position: string; logo_opacity: number;
  per_day: number; min_gap: number; max_claude: number; min_s: number; max_s: number; min_score: number;
  max_clips: number; subtitle_preset: Campaign["subtitle_preset"]; output_dir: string;
}

const BLANK: Campaign = {
  id: "", name: "", language: "auto", mode: "clip_source", sources: [], required_hashtags: [], required_mentions: [],
  target_accounts: [], limits: { per_day: 3, min_gap_minutes: 60, max_claude_requests_per_source: 10 },
  clip: { min_s: 30, max_s: 55, min_score: 70, max_clips_per_source: 3 }, subtitle_preset: "classic", output_dir: "output",
};

function toForm(c: Campaign): CampaignForm {
  return {
    id: c.id, name: c.name, language: c.language, mode: c.mode, sources: c.sources.join("\n"),
    hashtags: c.required_hashtags.join(" "), mentions: c.required_mentions.join(" "),
    accounts: c.target_accounts.map((a) => [a.platform, a.account_label, a.device_id].filter(Boolean).join(" ")).join("\n"),
    logo_path: c.logo?.path ?? "", logo_position: c.logo?.position ?? "top-right", logo_opacity: c.logo?.opacity ?? 1,
    per_day: c.limits.per_day, min_gap: c.limits.min_gap_minutes, max_claude: c.limits.max_claude_requests_per_source,
    min_s: c.clip.min_s, max_s: c.clip.max_s, min_score: c.clip.min_score, max_clips: c.clip.max_clips_per_source,
    subtitle_preset: c.subtitle_preset, output_dir: c.output_dir,
  };
}

const words = (s: string) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

function fromForm(f: CampaignForm): Campaign {
  return {
    id: f.id, name: f.name, language: f.language, mode: f.mode, sources: lines(f.sources),
    required_hashtags: words(f.hashtags), required_mentions: words(f.mentions),
    target_accounts: lines(f.accounts).map((l) => {
      const [platform, account_label, device_id] = words(l);
      return device_id ? { platform, account_label, device_id } : { platform, account_label };
    }),
    logo: f.logo_path ? { path: f.logo_path, position: f.logo_position, opacity: f.logo_opacity } : undefined,
    limits: { per_day: f.per_day, min_gap_minutes: f.min_gap, max_claude_requests_per_source: f.max_claude },
    clip: { min_s: f.min_s, max_s: f.max_s, min_score: f.min_score, max_clips_per_source: f.max_clips },
    subtitle_preset: f.subtitle_preset, output_dir: f.output_dir,
  };
}

function CampaignsPage({ campaigns, onSaved, setCid, cid, notify, version }: {
  campaigns: CampaignSummary[]; onSaved: () => void; setCid: (id: string) => void; cid: string; notify: Notify; version: number;
}) {
  const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState<CampaignForm | null>(null);
  const { data } = useLoad<Campaign>(cid && !isNew ? `/api/campaigns/${cid}` : null, notify, 0);

  useEffect(() => {
    if (data && !isNew) setForm(toForm(data));
  }, [data, isNew]);

  const set = <K extends keyof CampaignForm>(key: K, value: CampaignForm[K]) =>
    setForm((f) => (f ? { ...f, [key]: value } : f));

  const newCampaign = () => {
    setIsNew(true);
    setForm(toForm(BLANK));
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    try {
      await api(`/api/campaigns/${form.id}`, sendJson("PUT", fromForm(form)));
      setIsNew(false);
      setCid(form.id);
      onSaved();
      notify(`Кампания ${form.id} сохранена в campaigns/${form.id}.yaml`);
    } catch (err) {
      notify(await errorText(err), true);
    }
  };

  const run = async (path: string, label: string) => {
    try {
      const r = await api<{ job: number }>(path, { method: "POST" });
      notify(`${label}: задача #${r.job} поставлена в очередь`);
    } catch (err) {
      notify(await errorText(err), true);
    }
  };

  return (
    <div className="split">
      <section className="card">
        <div className="row">
          <h2>Кампании</h2>
          <button onClick={newCampaign}>Новая</button>
        </div>
        <ul className="list">
          {campaigns.map((c) => (
            <li key={c.id} className={c.id === cid && !isNew ? "selected" : ""}
                onClick={() => { setIsNew(false); setCid(c.id); }}>
              <b>{c.name ?? c.id}</b>
              {c.error ? <div className="error small">{c.error}</div> : <div className="muted small">{c.mode}</div>}
            </li>
          ))}
        </ul>
      </section>
      <section className="card">
        {!form && <p className="muted">Выберите кампанию или создайте новую.</p>}
        {form && (
          <form onSubmit={save} className="form">
            <h2>{isNew ? "Новая кампания" : form.name || form.id}</h2>
            <div className="grid2">
              <label>id<input required pattern="[a-z0-9_-]+" value={form.id} disabled={!isNew}
                              onChange={(e) => set("id", e.target.value)} /></label>
              <label>Название<input required value={form.name} onChange={(e) => set("name", e.target.value)} /></label>
              <label>Язык<input value={form.language} onChange={(e) => set("language", e.target.value)} /></label>
              <label>Режим
                <select value={form.mode} onChange={(e) => set("mode", e.target.value as Campaign["mode"])}>
                  <option value="clip_source">clip_source — нарезка исходников</option>
                  <option value="logo_overlay">logo_overlay — логотип поверх контента</option>
                </select>
              </label>
            </div>
            <label>Исходники (по одному пути на строку: файл или папка)
              <textarea rows={3} value={form.sources} onChange={(e) => set("sources", e.target.value)} />
            </label>
            <div className="grid2">
              <label>Обязательные хэштеги<input value={form.hashtags} onChange={(e) => set("hashtags", e.target.value)} /></label>
              <label>Обязательные упоминания<input value={form.mentions} onChange={(e) => set("mentions", e.target.value)} /></label>
            </div>
            <label>Аккаунты: «платформа метка [serial устройства]», по одному на строку
              <textarea rows={2} value={form.accounts} onChange={(e) => set("accounts", e.target.value)} />
            </label>
            <div className="grid2">
              <label>Логотип (путь, пусто — без логотипа)<input value={form.logo_path} onChange={(e) => set("logo_path", e.target.value)} /></label>
              <label>Прозрачность логотипа<input type="number" step="0.05" min={0} max={1} value={form.logo_opacity}
                                                onChange={(e) => set("logo_opacity", Number(e.target.value))} /></label>
            </div>
            <div className="grid4">
              <label>Клипов в день<input type="number" min={1} value={form.per_day} onChange={(e) => set("per_day", Number(e.target.value))} /></label>
              <label>Пауза, мин<input type="number" min={0} value={form.min_gap} onChange={(e) => set("min_gap", Number(e.target.value))} /></label>
              <label>Запросов Claude на исходник<input type="number" min={1} value={form.max_claude} onChange={(e) => set("max_claude", Number(e.target.value))} /></label>
              <label>Клипов с исходника<input type="number" min={1} value={form.max_clips} onChange={(e) => set("max_clips", Number(e.target.value))} /></label>
              <label>Мин. длительность, с<input type="number" min={1} value={form.min_s} onChange={(e) => set("min_s", Number(e.target.value))} /></label>
              <label>Макс. длительность, с<input type="number" min={1} value={form.max_s} onChange={(e) => set("max_s", Number(e.target.value))} /></label>
              <label>Порог score<input type="number" min={0} max={100} value={form.min_score} onChange={(e) => set("min_score", Number(e.target.value))} /></label>
              <label>Пресет субтитров
                <select value={form.subtitle_preset} onChange={(e) => set("subtitle_preset", e.target.value as Campaign["subtitle_preset"])}>
                  <option value="classic">classic</option><option value="bold">bold</option><option value="karaoke">karaoke</option>
                </select>
              </label>
            </div>
            <label>Папка вывода<input value={form.output_dir} onChange={(e) => set("output_dir", e.target.value)} /></label>
            <div className="row">
              <button type="submit">Сохранить</button>
              {!isNew && cid && (
                <>
                  <button type="button" onClick={() => run(`/api/campaigns/${cid}/process`, "Обработка")}>Обработать</button>
                  <button type="button" onClick={() => run(`/api/campaigns/${cid}/publish-now`, "Публикация")}>Опубликовать сейчас</button>
                </>
              )}
            </div>
          </form>
        )}
      </section>
    </div>
  );
}

// ---------- Sources ----------------------------------------------------------

function SourcesPage({ cid, notify, version }: { cid: string; notify: Notify; version: number }) {
  const { data, reload } = useLoad<Source[]>(cid ? `/api/campaigns/${cid}/sources` : null, notify, version);
  const [folder, setFolder] = useState("");

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    try {
      await api(`/api/campaigns/${cid}/sources/upload`, { method: "POST", body: fd });
      notify(`Загружено файлов: ${files.length}`);
      reload();
    } catch (err) {
      notify(await errorText(err), true);
    }
  };

  const addFolder = async () => {
    try {
      await api(`/api/campaigns/${cid}/sources/path`, sendJson("POST", { path: folder.trim() }));
      setFolder("");
      notify("Путь добавлен в кампанию");
      reload();
    } catch (err) {
      notify(await errorText(err), true);
    }
  };

  if (!cid) return <p className="muted">Сначала выберите кампанию.</p>;
  return (
    <section className="card">
      <h2>Исходники</h2>
      <div className="grid2">
        <label>Загрузить видеофайлы<input type="file" multiple accept="video/*" onChange={(e) => upload(e.target.files)} /></label>
        <label>Или путь к файлу/папке на этом компьютере
          <div className="row">
            <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="C:\\videos\\source" />
            <button onClick={addFolder} disabled={!folder.trim()}>Добавить</button>
          </div>
        </label>
      </div>
      <p className="muted small">«Обработать» в шапке кампании регистрирует файлы, транскрибирует и отбирает клипы.</p>
      <table>
        <thead><tr><th>Файл</th><th>Длительность</th><th>Разрешение</th><th>Аудио</th><th>Статус</th><th>Транскрипт</th></tr></thead>
        <tbody>
          {(data ?? []).map((s) => (
            <tr key={s.id}>
              <td className="wrap">{s.path}</td>
              <td>{s.duration_s.toFixed(1)} с</td>
              <td>{s.width}×{s.height}</td>
              <td>{s.has_audio ? "да" : "нет"}</td>
              <td>{s.status}</td>
              <td>{s.transcribed ? "готов" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.length === 0 && <p className="muted">Исходников пока нет.</p>}
    </section>
  );
}

// ---------- Queue ------------------------------------------------------------

function QueuePage({ cid, notify, version }: { cid: string; notify: Notify; version: number }) {
  const { data, reload } = useLoad<Clip[]>(cid ? `/api/campaigns/${cid}/clips` : null, notify, version);
  const [filter, setFilter] = useState("all");
  if (!cid) return <p className="muted">Сначала выберите кампанию.</p>;
  const shown = (data ?? []).filter((c) => filter === "all" || c.status === filter);
  return (
    <section>
      <div className="row">
        <h2>Очередь клипов</h2>
        <label>Статус{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {["all", "selected", "rendered", "ready", "approved", "rejected"].map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
      </div>
      <div className="clips">
        {shown.map((c) => <ClipCard key={c.id} clip={c} notify={notify} onChange={reload} />)}
      </div>
      {data && shown.length === 0 && <p className="muted">Клипов с таким статусом нет.</p>}
    </section>
  );
}

function ClipCard({ clip, notify, onChange }: { clip: Clip; notify: Notify; onChange: () => void }) {
  const [title, setTitle] = useState(clip.title_overlay);
  const [description, setDescription] = useState(clip.description);
  const [tags, setTags] = useState(clip.hashtags.join(" "));
  const [preset, setPreset] = useState(clip.subtitle_preset ?? "classic");

  const patch = async (body: Record<string, unknown>, done: string) => {
    try {
      await api(`/api/clips/${clip.id}`, sendJson("PATCH", body));
      notify(done);
      onChange();
    } catch (err) {
      notify(await errorText(err), true);
    }
  };

  const edits = { title_overlay: title, description, hashtags: words(tags), subtitle_preset: preset };
  return (
    <article className={`clip ${clip.status}`}>
      <video controls preload="metadata" src={`/api/clips/${clip.id}/video`} />
      <div className="clip-body">
        <div className="row">
          <b>#{clip.id}</b> <span className="muted small">score {clip.score} · {clip.hook_type} · {clip.start.toFixed(0)}–{clip.end.toFixed(0)} с</span>
          <span className={`badge ${clip.status}`}>{clip.status}</span>
        </div>
        {clip.reject_reason && <div className="error small">{clip.reject_reason}</div>}
        <label>Заголовок<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label>Описание<textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label>Хэштеги<input value={tags} onChange={(e) => setTags(e.target.value)} /></label>
        <label>Пресет субтитров
          <select value={preset} onChange={(e) => setPreset(e.target.value as Clip["subtitle_preset"] & string)}>
            <option value="classic">classic</option><option value="bold">bold</option><option value="karaoke">karaoke</option>
          </select>
        </label>
        <div className="row wrap-row">
          <button onClick={() => patch({ status: "approved" }, `Клип #${clip.id} одобрен`)}
                  disabled={!["ready", "rejected"].includes(clip.status)}>Одобрить</button>
          <button className="danger" onClick={() => patch({ status: "rejected" }, `Клип #${clip.id} отклонён`)}
                  disabled={!["ready", "approved"].includes(clip.status)}>Отклонить</button>
          <button onClick={() => patch(edits, "Сохранено")}>Сохранить</button>
          <button onClick={() => patch({ ...edits, rerender: true }, "Перерендер поставлен в очередь")}>Сохранить и перерендерить</button>
        </div>
      </div>
    </article>
  );
}

// ---------- Publishing -------------------------------------------------------

function PublishPage({ cid, notify, version }: { cid: string; notify: Notify; version: number }) {
  const { data } = useLoad<Publication[]>(cid ? `/api/campaigns/${cid}/publications` : null, notify, version);
  const publishNow = async () => {
    try {
      const r = await api<{ job: number }>(`/api/campaigns/${cid}/publish-now`, { method: "POST" });
      notify(`Публикация: задача #${r.job} поставлена в очередь`);
    } catch (err) {
      notify(await errorText(err), true);
    }
  };
  if (!cid) return <p className="muted">Сначала выберите кампанию.</p>;
  return (
    <section className="card">
      <div className="row">
        <h2>Публикация</h2>
        <button onClick={publishNow}>Опубликовать сейчас</button>
      </div>
      <p className="muted small">
        Клипы публикуются по расписанию. «Сейчас» переносит первый элемент каждого аккаунта на текущее время;
        дневной лимит и пауза между публикациями всё равно действуют.
      </p>
      <table>
        <thead><tr><th>Клип</th><th>Платформа</th><th>Аккаунт</th><th>Статус</th><th>Попытки</th><th>По расписанию</th><th>Опубликовано</th><th>Ошибка / id</th></tr></thead>
        <tbody>
          {(data ?? []).map((p) => (
            <tr key={p.id}>
              <td>#{p.clip_id} {p.title_overlay}</td>
              <td>{p.platform}</td>
              <td>{p.account_label}</td>
              <td><span className={`badge ${p.status}`}>{p.status}</span></td>
              <td>{p.attempts}</td>
              <td>{p.scheduled_at}</td>
              <td>{p.published_at ?? "—"}</td>
              <td className="wrap">{p.last_error ?? p.external_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.length === 0 && <p className="muted">Очередь пуста: одобрите клипы на странице «Очередь клипов».</p>}
    </section>
  );
}

// ---------- Analytics --------------------------------------------------------

const GROUPS = ["hook_type", "subtitle_preset", "speed_factor", "has_teaser", "has_callouts", "zoom_enabled"];

function AnalyticsPage({ cid, notify, version }: { cid: string; notify: Notify; version: number }) {
  const [by, setBy] = useState("hook_type");
  const groups = useLoad<AnalyticsRow[]>(cid ? `/api/campaigns/${cid}/analytics?by=${by}` : null, notify, version);
  const pubs = useLoad<Publication[]>(cid ? `/api/campaigns/${cid}/publications` : null, notify, version);

  const importCsv = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const fd = new FormData();
    fd.append("file", files[0]);
    try {
      const r = await api<{ imported: number }>("/api/metrics/import", { method: "POST", body: fd });
      notify(`Импортировано строк: ${r.imported}`);
      pubs.reload();
      groups.reload();
    } catch (err) {
      notify(await errorText(err), true);
    }
  };

  if (!cid) return <p className="muted">Сначала выберите кампанию.</p>;
  return (
    <section>
      <section className="card">
        <div className="row">
          <h2>Аналитика</h2>
          <label>Группировать по{" "}
            <select value={by} onChange={(e) => setBy(e.target.value)}>
              {GROUPS.map((g) => <option key={g}>{g}</option>)}
            </select>
          </label>
        </div>
        <table>
          <thead><tr><th>{by}</th><th>Публикаций</th><th>Просмотры</th><th>Лайки</th><th>Комментарии</th></tr></thead>
          <tbody>
            {(groups.data ?? []).map((r) => (
              <tr key={String(r.key)}>
                <td>{String(r.key)}</td><td>{r.publications}</td><td>{r.views}</td><td>{r.likes}</td><td>{r.comments}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {groups.data && groups.data.length === 0 && <p className="muted">Опубликованных клипов пока нет.</p>}
      </section>
      <section className="card">
        <div className="row">
          <h2>Метрики публикаций</h2>
          <label>Импорт CSV (publication_id,views,likes,comments)<input type="file" accept=".csv" onChange={(e) => importCsv(e.target.files)} /></label>
        </div>
        <table>
          <thead><tr><th>#</th><th>Клип</th><th>Аккаунт</th><th>Просмотры</th><th>Лайки</th><th>Комментарии</th><th /></tr></thead>
          <tbody>
            {(pubs.data ?? []).filter((p) => p.status === "published").map((p) => (
              <MetricsRow key={`${p.id}-${p.views}-${p.likes}-${p.comments}`} pub={p} notify={notify} onSaved={pubs.reload} />
            ))}
          </tbody>
        </table>
      </section>
    </section>
  );
}

function MetricsRow({ pub, notify, onSaved }: { pub: Publication; notify: Notify; onSaved: () => void }) {
  const [v, setV] = useState({ views: pub.views, likes: pub.likes, comments: pub.comments });
  const save = async () => {
    try {
      await api(`/api/publications/${pub.id}/metrics`, sendJson("PUT", v));
      notify("Метрики сохранены");
      onSaved();
    } catch (err) {
      notify(await errorText(err), true);
    }
  };
  const num = (k: keyof typeof v) => (
    <td><input type="number" min={0} value={v[k]} onChange={(e) => setV({ ...v, [k]: Number(e.target.value) })} /></td>
  );
  return (
    <tr>
      <td>{pub.id}</td><td>#{pub.clip_id} {pub.title_overlay}</td><td>{pub.account_label}</td>
      {num("views")}{num("likes")}{num("comments")}
      <td><button onClick={save}>Сохранить</button></td>
    </tr>
  );
}

// ---------- Settings ---------------------------------------------------------

function SettingsPage({ notify }: { notify: Notify }) {
  const { data, reload } = useLoad<Settings>("/api/settings", notify, 0);
  const [draft, setDraft] = useState<Settings | null>(null);
  useEffect(() => {
    if (data) setDraft(data);
  }, [data]);
  if (!draft) return <p className="muted">Загрузка настроек…</p>;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setDraft({ ...draft, [k]: v });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api("/api/settings", sendJson("PUT", draft));
      notify("Настройки сохранены в config.yaml");
      reload();
    } catch (err) {
      notify(await errorText(err), true);
    }
  };

  return (
    <section className="card">
      <h2>Настройки</h2>
      <form onSubmit={save} className="form">
        <div className="grid2">
          <label>Модель Whisper
            <select value={draft.whisper_model} onChange={(e) => set("whisper_model", e.target.value as Settings["whisper_model"])}>
              {["tiny", "base", "small", "medium", "large-v3"].map((m) => <option key={m}>{m}</option>)}
            </select>
          </label>
          <label>Кандидатов после префильтра<input type="number" min={1} value={draft.prefilter_top_n}
                                                  onChange={(e) => set("prefilter_top_n", Number(e.target.value))} /></label>
          <label>Путь к ffmpeg (или просто «ffmpeg», если он в PATH)<input value={draft.ffmpeg_path}
                                                                         onChange={(e) => set("ffmpeg_path", e.target.value)} /></label>
          <label>Путь к adb<input value={draft.adb_path} onChange={(e) => set("adb_path", e.target.value)} /></label>
          <label>Лимит запросов Claude за 24 часа<input type="number" min={1} value={draft.claude_daily_limit}
                                                        onChange={(e) => set("claude_daily_limit", Number(e.target.value))} /></label>
        </div>
        <button type="submit">Сохранить</button>
      </form>
    </section>
  );
}
