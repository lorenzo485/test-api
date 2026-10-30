"""Live traffic-light page for the production lab, served at `/` to browsers.

A single inline page (no CDN, no templates) that polls `/lab/status` every
2 s, so each lab step visibly turns its light green on the projector. The
audience is management, so the copy is plain business Spanish; technical
detail stays small and grey for the instructor.
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>test-api · production lab</title>
<style>
  :root { --bg:#0d1117; --card:#161b22; --fg:#e6edf3; --muted:#8b949e;
    --green:#2ea043; --red:#f85149; --amber:#d29922; --grey:#6e7681; }
  * { box-sizing:border-box; }
  body { margin:0; padding:2rem; background:var(--bg); color:var(--fg);
    font:1.25rem/1.4 system-ui, sans-serif; }
  #banner { display:none; background:var(--red); color:#fff; padding:.75rem 1rem;
    border-radius:.5rem; margin-bottom:1.5rem; font-weight:700; }
  #banner.show { display:block; }
  #verdict { border-radius:1rem; padding:1.5rem 2rem; margin-bottom:1.5rem;
    background:var(--card); border-left:1rem solid var(--grey); }
  #verdict.green { border-color:var(--green); }
  #verdict.amber { border-color:var(--amber); }
  #verdict.red { border-color:var(--red); }
  #verdict-text { font:800 clamp(2.2rem,6vw,4rem)/1.1 system-ui, sans-serif; }
  #verdict-count { font-size:1.6rem; color:var(--muted); margin-top:.5rem; }
  #version { margin-bottom:1.5rem; padding:.75rem 1rem; border-radius:.75rem;
    transition:background .6s; }
  #version.changed { background:#5a4a12; }
  #version .label { color:var(--muted); font-size:1.1rem; }
  #revision { font:700 clamp(1.6rem,4vw,2.8rem) ui-monospace, monospace;
    word-break:break-all; }
  #version-note { color:var(--amber); font-weight:700; min-height:1.6rem; }
  #hello { font-size:1.4rem; color:var(--muted); }
  .grid { display:grid; gap:1.25rem;
    grid-template-columns:repeat(auto-fit,minmax(18rem,1fr)); }
  .card { background:var(--card); border-radius:1rem; padding:1.5rem;
    display:flex; gap:1.25rem; align-items:flex-start; }
  .card > div:last-child { flex:1; min-width:0; }
  .dot { flex:none; width:3.5rem; height:3.5rem; border-radius:50%;
    background:var(--grey); transition:background .6s, box-shadow .6s; }
  .dot.green { background:var(--green); box-shadow:0 0 1.5rem var(--green); }
  .dot.red { background:var(--red); box-shadow:0 0 1.5rem var(--red); }
  .dot.amber { background:var(--amber); box-shadow:0 0 1.5rem var(--amber); }
  .card h2 { margin:0 0 .4rem; font-size:1.4rem; }
  .answer { font-size:1.5rem; font-weight:700; }
  .why { color:var(--fg); opacity:.8; font-size:1.05rem; margin-top:.4rem; }
  .tech { color:var(--muted); font:.85rem ui-monospace, monospace;
    margin-top:.5rem; word-break:break-all; }
  .bar { height:.7rem; background:#30363d; border-radius:.35rem; margin-top:.5rem; }
  .bar div { height:100%; width:0; background:var(--amber); border-radius:.35rem;
    transition:width .6s; }
  @media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
</style>
</head>
<body>
<div id="banner">Sin conexión con el servicio: reintentando…</div>
<section id="verdict">
  <div id="verdict-text">Consultando…</div>
  <div id="verdict-count"></div>
</section>
<section id="version">
  <div class="label">Versión en producción</div>
  <div id="revision">…</div>
  <div id="version-note"></div>
  <div id="hello"></div>
</section>
<main class="grid">
  <div class="card"><div class="dot" id="region-dot"></div><div>
    <h2>¿Corre donde la empresa lo permite?</h2>
    <div class="answer" id="region"></div>
    <div class="why">Por qué importa: cumplimiento y residencia de datos.</div>
    <div class="tech" id="region-tech"></div></div></div>
  <div class="card"><div class="dot" id="legacy-dot"></div><div>
    <h2>¿Llega a los sistemas internos?</h2>
    <div class="answer" id="legacy"></div>
    <div class="why">Por qué importa: sin esto el servicio no tiene datos reales.</div>
    <div class="tech" id="legacy-tech">bd-clientes.gtd.corp</div></div></div>
  <div class="card"><div class="dot" id="secret-dot"></div><div>
    <h2>¿Tiene sus credenciales seguras?</h2>
    <div class="answer" id="secret"></div>
    <div class="why">Por qué importa: claves fuera del código, nunca visibles.</div>
    <div class="tech" id="secret-tech">DEMO_API_KEY (Secret Manager)</div>
  </div></div>
  <div class="card"><div class="dot" id="warmup-dot"></div><div>
    <h2>¿Está listo para atender?</h2>
    <div class="answer" id="warmup"></div>
    <div class="bar"><div id="warmup-bar"></div></div>
    <div class="why">Por qué importa: los clientes no ven errores mientras arranca.</div>
    <div class="tech" id="warmup-tech"></div></div></div>
  <div class="card"><div class="dot" id="user-dot"></div><div>
    <h2>¿Solo entra personal autorizado?</h2>
    <div class="answer" id="user"></div>
    <div class="why">Por qué importa: solo cuentas corporativas.</div>
    <div class="tech" id="user-tech">Identity-Aware Proxy (IAP)</div></div></div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let lastRevision = null;
let noteTimer = null;
function light(id, color, text, tech) {
  $(id + "-dot").className = "dot " + color;
  $(id).textContent = text;
  if (tech !== undefined) $(id + "-tech").textContent = tech;
  return color === "green";
}
const LEGACY = {
  reachable: ["green", "Conectado a la base de clientes"],
  unreachable: ["red", "Sin conexión"],
  not_configured: ["", "No configurado"],
};
async function poll() {
  try {
    const response = await fetch("/lab/status", { cache: "no-store" });
    if (!response.ok) throw new Error(response.status);
    const s = await response.json();
    if (lastRevision !== null && s.revision !== lastRevision) {
      $("version").classList.add("changed");
      $("version-note").textContent = "Cambió la versión en producción";
      clearTimeout(noteTimer);
      noteTimer = setTimeout(() => {
        $("version").classList.remove("changed");
        $("version-note").textContent = "";
      }, 6000);
    }
    lastRevision = s.revision;
    $("revision").textContent = s.revision;
    $("hello").textContent = s.user ? "Hola, " + s.user : "";
    const pct = Math.round(s.warmup.progress * 100);
    const greens = [
      light("region", s.region_ok ? "green" : "red",
        s.region_ok ? "Sí, en Santiago" : "No, fuera de la región autorizada",
        s.region),
      light("legacy", ...LEGACY[s.legacy]),
      light("secret", s.secret ? "green" : "red",
        s.secret ? "Sí, desde el gestor de secretos" : "Falta la credencial"),
      light("warmup", s.warmup.done ? "green" : "amber",
        s.warmup.done ? "Listo" : "Preparándose… " + pct + " %",
        s.service + " · " + s.environment),
      light("user", s.user ? "green" : "red",
        s.user ? "Acceso verificado: " + s.user : "Sin control de acceso"),
    ].filter(Boolean).length;
    $("warmup-bar").style.width = pct + "%";
    $("warmup-bar").style.background = s.warmup.done ? "var(--green)" : "var(--amber)";
    const ready = greens === 5;
    $("verdict").className = ready ? "green" : greens >= 3 ? "amber" : "red";
    $("verdict-text").textContent = ready ? "Listo para clientes" : "Aún no está listo";
    $("verdict-count").textContent = greens + " de 5 controles en verde";
    $("banner").classList.remove("show");
  } catch (error) {
    $("banner").classList.add("show");
  }
}
poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""
