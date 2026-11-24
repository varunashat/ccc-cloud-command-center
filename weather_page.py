"""Small standalone page for the weather-forecast Foundry agent, served at /weather behind the app sign-in."""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weather forecast</title>
<style>body{margin:0;font:16px system-ui,Segoe UI,sans-serif;background:#0b1220;color:#e6edf7}
main{max-width:720px;margin:8vh auto;padding:0 20px}h1{font-size:26px}
textarea{width:100%;box-sizing:border-box;min-height:90px;padding:12px;border-radius:10px;border:1px solid #2a3a57;background:#111b2e;color:inherit;font:inherit}
button{margin-top:12px;padding:10px 22px;border:0;border-radius:10px;background:#38bdf8;color:#04121f;font-weight:600;cursor:pointer}
#out{margin-top:20px;white-space:pre-wrap;line-height:1.5}a{color:#7dd3fc}small{color:#93a4bf}</style></head>
<body><main><h1>Weather forecast</h1>
<p><small>Answers come from the weather-forecast agent in Azure AI Foundry (3-day forecast from Open-Meteo).</small></p>
<textarea id="q" maxlength="800" aria-label="Weather question">Tomorrow's weather in Sharjah</textarea>
<button id="go">Get forecast</button><div id="out" role="status"></div>
<script>
const token="__TOKEN__";
document.getElementById("go").onclick=async()=>{
  const out=document.getElementById("out");out.textContent="Getting forecast...";
  try{
    const r=await fetch("/api/supervisor",{method:"POST",headers:{"Content-Type":"application/json","X-Local-Token":token},
      body:JSON.stringify({action:"weather",prompt:document.getElementById("q").value,consent:true})});
    const d=await r.json();if(!r.ok)throw new Error(d.error||"Forecast unavailable.");
    out.textContent=d.answer;
    for(const s of d.sources||[]){const a=document.createElement("a");a.href=s;a.target="_blank";a.rel="noopener";a.textContent="Forecast source";out.append(document.createElement("br"),a);}
  }catch(e){out.textContent=e.message;}
};
</script></main></body></html>"""
