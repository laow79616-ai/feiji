(function(){
  async function attachGroupNotes(){
    var nav=document.getElementById("groupNav");
    if(!nav) return;
    var notes={};
    try{ notes=await (await fetch("/pools/proxy-notes")).json(); }catch(e){ notes={}; }
    var boxes=nav.querySelectorAll(":scope > div");
    boxes.forEach(function(div){
      if(div.querySelector(".note-row")) return;
      var title=div.querySelector(".text-sm, .font-medium, div");
      var txt=(title && title.textContent)?title.textContent:div.innerText;
      var line=(txt||"").split("\n")[0];
      var i=line.indexOf("·");
      var name=(i>=0?line.slice(i+1):line).trim();
      if(!name || name.indexOf("搜索")>=0) return;
      var row=document.createElement("div");
      row.className="note-row flex items-center gap-1 mt-1";
      row.addEventListener("click", function(e){ e.stopPropagation(); });
      var input=document.createElement("input");
      input.className="flex-1 min-w-0 px-1 py-0.5 rounded text-xs";
      input.placeholder="备注";
      input.value=notes[name]||"";
      var btn=document.createElement("button");
      btn.type="button";
      btn.className="text-xs px-2 py-0.5 bg-blue-700 rounded";
      btn.textContent="保存";
      var tip=document.createElement("span");
      tip.className="text-xs text-green-400";
      btn.onclick=function(){
        fetch("/pools/proxies/note-by-name",{
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({name:name, note:input.value.trim()})
        }).then(function(){
          tip.textContent="已保存";
          setTimeout(function(){ tip.textContent=""; },1500);
        });
      };
      row.appendChild(input); row.appendChild(btn); row.appendChild(tip);
      div.appendChild(row);
    });
  }
  function hook(){
    if(window.__notesHooked) return;
    if(typeof window.loadAccounts!=="function") return;
    window.__notesHooked=true;
    var old=window.loadAccounts;
    window.loadAccounts=async function(){
      var r=await old.apply(this, arguments);
      setTimeout(attachGroupNotes, 50);
      return r;
    };
  }
  hook();
  var n=0;
  var t=setInterval(function(){ hook(); if(window.__notesHooked || ++n>40) clearInterval(t); }, 250);
  document.addEventListener("DOMContentLoaded", function(){ setTimeout(attachGroupNotes, 1000); });
  var nav=document.getElementById("groupNav");
  if(nav && window.MutationObserver){
    new MutationObserver(function(){ setTimeout(attachGroupNotes, 30); }).observe(nav,{childList:true});
  }
})();
