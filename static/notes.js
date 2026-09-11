
(function(){
  async function attachGroupNotes(){
    let notes={};
    try{ notes=await (await fetch("/pools/proxy-notes")).json(); }catch(e){ notes={}; }
    const nav=document.getElementById("groupNav");
    if(!nav) return;
    nav.querySelectorAll(":scope > div").forEach(div=>{
      if(div.querySelector(".note-row")) return;
      const title=div.querySelector(".text-sm");
      if(!title) return;
      const txt=(title.textContent||"");
      const i=txt.indexOf("·");
      const name=(i>=0?txt.slice(i+1):txt).trim();
      if(!name) return;
      const row=document.createElement("div");
      row.className="note-row flex items-center gap-1 mt-1";
      row.onclick=function(e){ e.stopPropagation(); };
      const input=document.createElement("input");
      input.className="flex-1 min-w-0 px-1 py-0.5 rounded text-xs";
      input.placeholder="备注";
      input.value=notes[name]||"";
      const btn=document.createElement("button");
      btn.type="button";
      btn.className="text-xs px-2 py-0.5 bg-blue-700 rounded";
      btn.textContent="保存";
      const tip=document.createElement("span");
      tip.className="text-xs text-green-400";
      btn.onclick=function(){
        fetch("/pools/proxies/note-by-name",{
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({name:name, note:input.value.trim()})
        }).then(function(){ tip.textContent="已保存"; setTimeout(function(){ tip.textContent=""; },1500); });
      };
      row.appendChild(input); row.appendChild(btn); row.appendChild(tip);
      div.appendChild(row);
    });
  }
  const old=window.loadAccounts;
  if(typeof old==="function"){
    window.loadAccounts=async function(){
      const r=await old.apply(this, arguments);
      try{ await attachGroupNotes(); }catch(e){ console.log(e); }
      return r;
    };
  }
  document.addEventListener("DOMContentLoaded", function(){
    setTimeout(attachGroupNotes, 800);
  });
})();
