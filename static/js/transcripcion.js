/* static/js/transcripcion.js
 * ─────────────────────────────────────────────
 * VITACORE · Editor Clínico por Bloques — v3
 *
 * Nuevo en v3:
 *   ✓ Autocompletado clínico en los 5 bloques
 *   ✓ Términos del diccionario actualizan el AC en tiempo real
 *   ✓ Al agregar un término al diccionario → AC se actualiza
 * ─────────────────────────────────────────────
 */
(function () {
  "use strict";

  const JOB    = window.TX_JOB || {};
  const JOB_ID = JOB.id || "";

  const BLOQUES = ["paciente", "estudio", "tecnica", "hallazgos", "conclusion"];

  const editors = {};
  const state = {
    pendiente:    false,
    diccionario:  null,
    modoAtajos:   localStorage.getItem("tx_shortcut_mode") || "express",
    SEEK_SECS:    5,
    pollingTimer: null,
    pollingSegs:  0,
    terminado:    false,
    socket:       null,
    terminosAC:   [],   // términos para el autocompletado
  };

  const MODOS = {
    express: [
      { key:"F4",      label:"Stop",          icon:"fa-stop",         action:"stop"      },
      { key:"F7",      label:"Retroceder 5s", icon:"fa-backward",     action:"rewind"    },
      { key:"F9",      label:"Play / Pausa",  icon:"fa-play",         action:"playpause" },
      { key:"F10",     label:"Adelantar 5s",  icon:"fa-forward",      action:"forward"   },
      { key:"F11",     label:"Sig. dictado",  icon:"fa-forward-step", action:"next"      },
      { key:"Espacio", label:"Play / Pausa",  icon:"fa-play-pause",   action:"playpause" },
    ],
    custom: [
      { key:"F4",      label:"Stop",          icon:"fa-stop",         action:"stop"      },
      { key:"F7",      label:"Retroceder 5s", icon:"fa-backward",     action:"rewind"    },
      { key:"F9",      label:"Play / Pausa",  icon:"fa-play",         action:"playpause" },
      { key:"F10",     label:"Adelantar 5s",  icon:"fa-forward",      action:"forward"   },
      { key:"F11",     label:"Sig. dictado",  icon:"fa-forward-step", action:"next"      },
      { key:"Espacio", label:"Play / Pausa",  icon:"fa-play-pause",   action:"playpause" },
    ],
  };
  let keyMap = {};

  // ── REFS DOM ─────────────────────────────────────────────────
  const playerSection  = document.getElementById("playerSection");
  const audio          = document.getElementById("audio");
  const audioSrc       = document.getElementById("audioSrc");
  const pendingPill    = document.getElementById("pendingPill");
  const savedPill      = document.getElementById("savedPill");
  const vol            = document.getElementById("vol");
  const volLbl         = document.getElementById("volLbl");
  const speed          = document.getElementById("speed");
  const btnGuardar     = document.getElementById("btnGuardar");
  const btnReprocesar  = document.getElementById("btnReprocesar");
  const btnCopiar      = document.getElementById("btnCopiar");
  const btnWord        = document.getElementById("btnWord");
  const btnPdf         = document.getElementById("btnPdf");
  const scRows         = document.getElementById("scRows");
  const jobStatusBar   = document.getElementById("jobStatusBar");
  const jsbTexto       = document.getElementById("jsbTexto");
  const jsbIcon        = document.getElementById("jsbIcon");
  const jsbBar         = document.getElementById("jsbBar");
  const jsbTiempo      = document.getElementById("jsbTiempo");
  const jsbSegundos    = document.getElementById("jsbSegundos");
  const btnDiccionario = document.getElementById("btnDiccionario");
  const dictOverlay    = document.getElementById("dictOverlay");
  const dpClose        = document.getElementById("dpClose");
  const dpSearchInput  = document.getElementById("dpSearchInput");
  const dpSearchCount  = document.getElementById("dpSearchCount");
  const dpTermInput    = document.getElementById("dpTermInput");
  const dpCatSelect    = document.getElementById("dpCatSelect");
  const dpBtnAgregar   = document.getElementById("dpBtnAgregar");
  const dpBtnCorreccion= document.getElementById("dpBtnCorreccion");
  const dpList         = document.getElementById("dpList");
  const dpEmpty        = document.getElementById("dpEmpty");
  const dpTotalCount   = document.getElementById("dpTotalCount");
  const dpCatCount     = document.getElementById("dpCatCount");
  const dpToast        = document.getElementById("dpToast");

  // ════════════════════════════════════════════
  // TOOLBAR CKEDITOR — COMPLETA
  // ════════════════════════════════════════════
  const TOOLBAR_BLOQUE = {
    items: [
      "heading", "|",
      "bold", "italic", "underline", "|",
      "alignment", "|",
      "bulletedList", "numberedList", "|",
      "insertTable", "|",
      "findAndReplace", "|",
      "specialCharacters", "|",
      "undo", "redo"
    ],
    shouldNotGroupWhenFull: true,
  };

  // ════════════════════════════════════════════
  // INICIALIZAR 5 EDITORES + AUTOCOMPLETADO
  // ════════════════════════════════════════════
  async function inicializarEditores() {
    for (const bloque of BLOQUES) {
      try {
        const editor = await ClassicEditor.create(
          document.getElementById(`ck-${bloque}`),
          {
            language: "es",
            toolbar:  TOOLBAR_BLOQUE,
            placeholder: placeholderBloque(bloque),
          }
        );

        editors[bloque] = editor;

        editor.model.document.on("change:data", () => {
          actualizarEstadoBloque(bloque);
          if (typeof setPendiente === "function") setPendiente(true);
        });

        // Tab → siguiente bloque (solo cuando AC no está activo)
        editor.editing.view.document.on("keydown", (evt, data) => {
          // Si el dropdown de AC está visible, dejar que AC maneje Tab
          const acDropdown = document.getElementById("ac-dropdown");
          if (acDropdown && acDropdown.style.display !== "none") return;

          if (data.keyCode === 9) {
            data.preventDefault(); evt.stop();
            const idx     = BLOQUES.indexOf(bloque);
            const destino = data.shiftKey ? BLOQUES[idx - 1] : BLOQUES[idx + 1];
            if (destino && editors[destino]) {
              editors[destino].editing.view.focus();
            }
          }
        }, { priority: "low" }); // prioridad baja para que AC tenga prioridad alta

      } catch (err) {
        console.error(`[CKEditor] Error en bloque ${bloque}:`, err);
      }
    }

    console.log(`[TX] ${Object.keys(editors).length} editores listos`);

    // Cargar diccionario e inicializar autocompletado
    await cargarTerminosParaAC();

    // Si ya hay datos al cargar
    if (JOB.estado === "done") {
      if (JOB.estructura && Object.keys(JOB.estructura).length > 0) {
        cargarEstructura(JOB.estructura);
      } else if (JOB.html_clinico) {
        cargarDesdeHTML(JOB.html_clinico);
      }
    }
  }

  // ════════════════════════════════════════════
  // CARGAR TÉRMINOS Y ACTIVAR AUTOCOMPLETADO
  // ════════════════════════════════════════════
  async function cargarTerminosParaAC() {
    try {
      const resp = await fetch("/diccionario");
      const data = await resp.json();

      // Extraer lista plana de todos los términos
      const terminos = [];
      const esp = data.especialidades || {};
      Object.values(esp).forEach(info => {
        (info.terminos || []).forEach(t => {
          if (t && !terminos.includes(t)) terminos.push(t);
        });
      });

      state.terminos = terminos;

      // Inicializar autocompletado si el script está cargado
      if (window.initAutocompletado) {
        window.initAutocompletado(editors, terminos);
      }

      // También guardar para el panel diccionario
      state.diccionario = data;

    } catch (e) {
      console.warn("[AC] No se pudo cargar el diccionario:", e.message);
    }
  }

  function placeholderBloque(bloque) {
    return {
      paciente:   "Nombre completo e identificación del paciente...",
      estudio:    "Tipo de estudio (TAC, Rx, Ecografía, RM, Hemodinamia)...",
      tecnica:    "Técnica y protocolo utilizado...",
      hallazgos:  "Descripción detallada de hallazgos anatómicos y radiológicos...",
      conclusion: "Impresión diagnóstica o conclusión final...",
    }[bloque] || "";
  }

  // ════════════════════════════════════════════
  // CARGAR ESTRUCTURA JSON EN LOS BLOQUES
  // ════════════════════════════════════════════
  function cargarEstructura(estructura) {
    if (!estructura) return;
    BLOQUES.forEach(bloque => {
      const texto = (estructura[bloque] || "").trim();
      if (!texto || !editors[bloque]) return;
      const html = texto.split(/\n+/).filter(l => l.trim()).map(l => `<p>${l.trim()}</p>`).join("");
      editors[bloque].setData(html);
      actualizarEstadoBloque(bloque);
    });
    actualizarCompletitud();
  }

  // ════════════════════════════════════════════
  // PARSEAR HTML CLÍNICO → 5 BLOQUES
  // ════════════════════════════════════════════
  function cargarDesdeHTML(htmlClinico) {
    if (!htmlClinico) return;
    const parser = new DOMParser();
    const doc    = parser.parseFromString(htmlClinico, "text/html");
    const mapaH2 = {
      "paciente":"paciente","estudio":"estudio","técnica":"tecnica","tecnica":"tecnica",
      "hallazgo":"hallazgos","hallazgos":"hallazgos","conclusión":"conclusion","conclusion":"conclusion","impresión":"conclusion",
    };
    const headers = doc.querySelectorAll("h2");
    headers.forEach(h2 => {
      const textoH2 = h2.textContent.trim().toLowerCase();
      let bloque = null;
      for (const [clave, nombre] of Object.entries(mapaH2)) {
        if (textoH2.includes(clave)) { bloque = nombre; break; }
      }
      if (!bloque || !editors[bloque]) return;
      let contenido = "";
      let next = h2.nextElementSibling;
      while (next && next.tagName !== "H2") { contenido += next.outerHTML; next = next.nextElementSibling; }
      if (contenido.trim()) { editors[bloque].setData(contenido.trim()); actualizarEstadoBloque(bloque); }
    });

    const todosVacios = BLOQUES.every(b => {
      if (!editors[b]) return true;
      const div = document.createElement("div"); div.innerHTML = editors[b].getData();
      return !(div.textContent || "").trim();
    });
    if (todosVacios && htmlClinico.trim() && editors["hallazgos"]) {
      editors["hallazgos"].setData(htmlClinico); actualizarEstadoBloque("hallazgos");
    }
    actualizarCompletitud();
  }

  // ════════════════════════════════════════════
  // ESTADO VISUAL DE CADA BLOQUE
  // ════════════════════════════════════════════
  function actualizarEstadoBloque(bloque) {
    if (!editors[bloque]) return;
    const div = document.createElement("div");
    div.innerHTML = editors[bloque].getData();
    const chars = (div.textContent || div.innerText || "").trim().length;
    const charsEl = document.getElementById(`chars-${bloque}`);
    const badge   = document.getElementById(`badge-${bloque}`);
    const card    = document.getElementById(`bloque-${bloque}`);
    const dot     = document.getElementById(`dot-${bloque}`);
    if (charsEl) charsEl.textContent = `${chars} chars`;
    if (chars === 0) {
      if (badge) { badge.textContent="Vacío";    badge.className="bloque-badge vacio"; }
      if (card)  { card.classList.remove("bloque-done"); card.classList.add("bloque-vacio"); }
      if (dot)   dot.classList.remove("lleno");
    } else {
      if (badge) { badge.textContent="Completo"; badge.className="bloque-badge completo"; }
      if (card)  { card.classList.remove("bloque-vacio"); card.classList.add("bloque-done"); }
      if (dot)   dot.classList.add("lleno");
    }
    actualizarCompletitud();
  }

  function actualizarCompletitud() {
    let llenos = 0;
    BLOQUES.forEach(b => {
      if (!editors[b]) return;
      const div = document.createElement("div"); div.innerHTML = editors[b].getData();
      if ((div.textContent || "").trim().length > 0) llenos++;
    });
    const pct = document.getElementById("completitudPct");
    if (pct) pct.textContent = `${llenos} / ${BLOQUES.length} bloques`;
  }

  window.toggleBloque = function(bloque) {
    if (editors[bloque]) setTimeout(() => editors[bloque].editing.view.focus(), 100);
  };

  // ════════════════════════════════════════════
  // ENSAMBLAR INFORME COMPLETO
  // ════════════════════════════════════════════
  const NOMBRES_SECCION = {
    paciente:"PACIENTE", estudio:"ESTUDIO", tecnica:"TÉCNICA",
    hallazgos:"HALLAZGOS", conclusion:"CONCLUSIÓN",
  };

  function getInformeHTML() {
    let html = `<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#0f172a;">`;
    BLOQUES.forEach(b => {
      if (!editors[b]) return;
      const contenido = editors[b].getData().trim();
      if (!contenido) return;
      html += `<h2 style="color:#0f4c81;border-bottom:2px solid #0ea5ff;padding-bottom:4px;margin-top:18px;">${NOMBRES_SECCION[b]}</h2>`;
      html += contenido;
    });
    return html + `</div>`;
  }

  function getInformeTexto() {
    let texto = "";
    BLOQUES.forEach(b => {
      if (!editors[b]) return;
      const div = document.createElement("div"); div.innerHTML = editors[b].getData();
      const contenido = (div.textContent || div.innerText || "").trim();
      if (!contenido) return;
      texto += `\n${NOMBRES_SECCION[b]}\n${"─".repeat(40)}\n${contenido}\n`;
    });
    return texto.trim();
  }

  // ════════════════════════════════════════════
  // INIT
  // ════════════════════════════════════════════
  (function init() {
    renderAtajos(state.modoAtajos);
    if (JOB.audio_url) {
      audioSrc.src = JOB.audio_url; audioSrc.type = "audio/mpeg";
      audio.load(); playerSection.classList.add("visible");
    }
    inicializarEditores().then(() => {
      switch (JOB.estado) {
        case "done":
          state.terminado = true;
          btnGuardar.disabled = false;
          iniciarWebSocket();
          break;
        case "processing":
          mostrarProgreso("processing");
          iniciarWebSocket();
          iniciarPolling();
          break;
        case "error":
          mostrarError(JOB.error_mensaje||"Error");
          btnReprocesar.style.display = "inline-flex";
          break;
        default:
          encolarAutomaticamente();
      }
    });
  })();

  // ════════════════════════════════════════════
  // WEBSOCKET
  // ════════════════════════════════════════════
  function iniciarWebSocket() {
    if (!JOB_ID || !window.io) return;
    const socket = io({ transports:["websocket","polling"], reconnection:true });
    state.socket = socket;
    socket.on("connect", () => { console.log("[WS] Conectado —", socket.id); socket.emit("unirse_job", { job_id: JOB_ID }); });
    socket.on("job_listo", (data) => { if(data.job_id!==JOB_ID||state.terminado)return; detenerPolling(); onTranscripcionLista(data); });
    socket.on("job_error", (data) => { if(data.job_id!==JOB_ID||state.terminado)return; detenerPolling(); mostrarError(data.mensaje||"Error"); btnReprocesar.style.display="inline-flex"; });
  }

  // ════════════════════════════════════════════
  // POLLING
  // ════════════════════════════════════════════
  function iniciarPolling() {
    if (state.pollingTimer||state.terminado) return;
    state.pollingSegs=0; jsbTiempo.style.display="inline-flex";
    state.pollingTimer = setInterval(async () => {
      if (state.terminado) { detenerPolling(); return; }
      state.pollingSegs+=2; jsbSegundos.textContent=state.pollingSegs; actualizarStepsPorTiempo(state.pollingSegs);
      try {
        const resp=await fetch(`/transcripcion/${JOB_ID}/estado`);
        const data=await resp.json();
        if (!data.ok){detenerPolling();return;}
        if (data.estado==="done"&&!state.terminado){detenerPolling();onTranscripcionLista(data);}
        else if(data.estado==="error"&&!state.terminado){detenerPolling();mostrarError(data.error||"Error");btnReprocesar.style.display="inline-flex";}
      } catch(e){console.warn("[Polling]",e.message);}
    }, 2000);
  }

  function detenerPolling() { if(state.pollingTimer){clearInterval(state.pollingTimer);state.pollingTimer=null;} }

  function actualizarStepsPorTiempo(segs) {
    [{id:"step-upload",desde:0,hasta:5},{id:"step-ffmpeg",desde:5,hasta:15},
     {id:"step-stt",desde:15,hasta:40},{id:"step-llm",desde:40,hasta:60},{id:"step-done",desde:60,hasta:999}]
    .forEach(p=>{const el=document.getElementById(p.id);if(!el)return;el.classList.remove("active","done");
      if(segs>=p.hasta)el.classList.add("done");if(segs>=p.desde&&segs<p.hasta)el.classList.add("active");});
  }

  async function encolarAutomaticamente() {
    try {
      mostrarProgreso("pending"); iniciarWebSocket();
      const resp=await fetch(`/transcripcion/${JOB_ID}/procesar`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({especialidad:"radiologia",bloque:"general"})});
      const data=await resp.json();
      if(data.ok){mostrarProgreso("processing");iniciarPolling();}
      else{mostrarError(data.error||"No se pudo iniciar");btnReprocesar.style.display="inline-flex";}
    } catch(e){mostrarError("Error de red: "+e.message);btnReprocesar.style.display="inline-flex";}
  }

  // ════════════════════════════════════════════
  // CUANDO LA TRANSCRIPCIÓN ESTÁ LISTA
  // ════════════════════════════════════════════
  function onTranscripcionLista(data) {
    if (state.terminado) return;
    state.terminado = true;
    ["step-upload","step-ffmpeg","step-stt","step-llm","step-done"].forEach(id=>{
      const el=document.getElementById(id);if(el){el.classList.remove("active","error");el.classList.add("done");}
    });
    jsbBar.classList.remove("indeterminate"); jsbBar.style.width="100%";
    jsbIcon.className="fa-solid fa-circle-check"; jsbIcon.style.color="#22c55e";
    jsbTexto.textContent=`Completado en ${data.duracion||0}s`;
    setTimeout(()=>{jobStatusBar.style.opacity="0";jobStatusBar.style.transition="opacity .5s";setTimeout(()=>{jobStatusBar.classList.remove("visible");jobStatusBar.style.opacity="";},500);},4000);

    const cargar = () => {
      const listos = BLOQUES.filter(b=>editors[b]).length;
      if (listos===BLOQUES.length) {
        if(data.estructura&&Object.keys(data.estructura).length>0)cargarEstructura(data.estructura);
        else if(data.html_clinico)cargarDesdeHTML(data.html_clinico);
        btnGuardar.disabled=false; setPendiente(true);

        // SweetAlert con tiempo y bloques completados
        const duracion=data.duracion||0;
        const min=Math.floor(duracion/60),seg=Math.round(duracion%60);
        const tiempoStr=min>0?`${min}m ${seg}s`:`${seg}s`;
        let bloquesCont=0;
        BLOQUES.forEach(b=>{if(!editors[b])return;const d=document.createElement("div");d.innerHTML=editors[b].getData();if((d.textContent||"").trim().length>0)bloquesCont++;});
        if(window.Swal){
          Swal.fire({
            title:"¡Transcripción lista!",
            html:`<div style="text-align:center;padding:10px 0;">
              <div style="font-size:42px;font-weight:900;color:#0f4c81;line-height:1;">${tiempoStr}</div>
              <div style="font-size:13px;color:#64748b;margin-top:4px;">tiempo de procesamiento</div>
              <div style="margin-top:16px;display:flex;justify-content:center;gap:20px;">
                <div style="text-align:center;"><div style="font-size:24px;font-weight:800;color:#22c55e;">${bloquesCont}</div><div style="font-size:11px;color:#64748b;">bloques<br>completados</div></div>
                <div style="text-align:center;"><div style="font-size:24px;font-weight:800;color:#0ea5ff;">${BLOQUES.length}</div><div style="font-size:11px;color:#64748b;">bloques<br>totales</div></div>
              </div>
              <div style="margin-top:14px;font-size:12px;color:#94a3b8;">Revisa y edita cada bloque antes de guardar en HC</div>
            </div>`,
            icon:"success", confirmButtonText:"Revisar informe", confirmButtonColor:"#0f4c81",
            timer:8000, timerProgressBar:true,
          });
        }
      } else { setTimeout(cargar,200); }
    };
    cargar();
  }

  // ════════════════════════════════════════════
  // RE-PROCESAR / GUARDAR / EXPORTAR
  // ════════════════════════════════════════════
  btnReprocesar.addEventListener("click", async () => {
    btnReprocesar.style.display="none"; state.terminado=false; detenerPolling();
    if(state.socket){state.socket.disconnect();state.socket=null;}
    BLOQUES.forEach(b=>{if(editors[b]){editors[b].setData("");actualizarEstadoBloque(b);}});
    await encolarAutomaticamente();
  });

  btnGuardar.addEventListener("click", async () => {
    const html=getInformeHTML().trim(),texto=getInformeTexto().trim();
    if(!texto){notifyErr("Informe vacío","No hay contenido.");return;}
    setLoading(btnGuardar,"spinGuardar","icoGuardar",true);
    try{
      const resp=await fetch(`/transcripcion/${JOB_ID}/guardar`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({informe_html:html,informe_final:texto})});
      const data=await resp.json();
      if(data.ok){setPendiente(false);if(window.Swal)Swal.fire({title:"Guardado en Historia Clínica",text:"El informe fue almacenado correctamente.",icon:"success",timer:3000,showConfirmButton:false});}
      else notifyErr("Error al guardar",data.error||"Error desconocido");
    }catch(e){notifyErr("Error de red",e.message);}
    finally{setLoading(btnGuardar,"spinGuardar","icoGuardar",false);}
  });

  btnCopiar.addEventListener("click", async () => {
    const texto=getInformeTexto().trim();
    if(!texto){notifyErr("Informe vacío","No hay texto.");return;}
    try{await navigator.clipboard.writeText(texto);if(window.Toast)Toast.show("Copiado al portapapeles","success");}
    catch{notifyErr("Error","No se pudo copiar.");}
  });

  async function exportar(ext,spinId,icoId,btn) {
    const texto=getInformeTexto().trim();
    if(!texto){notifyErr("Informe vacío","No hay texto.");return;}
    setLoading(btn,spinId,icoId,true);
    const url=`/transcripcion/${JOB_ID}/exportar-${ext==="docx"?"word":"pdf"}`;
    try{
      const resp=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({texto,nombre:JOB.proc_nombre?.replace(/[^a-z0-9]/gi,"_")||"informe"})});
      if(!resp.ok)throw new Error(`HTTP ${resp.status}`);
      const blob=await resp.blob();
      const a=Object.assign(document.createElement("a"),{href:URL.createObjectURL(blob),download:`informe_${JOB.nro_interno||"vitacore"}.${ext}`});
      document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(a.href);
      if(window.Toast)Toast.show(`${ext.toUpperCase()} descargado`,"success");
      setPendiente(false);
    }catch(e){notifyErr(`Error exportar ${ext.toUpperCase()}`,e.message);}
    finally{setLoading(btn,spinId,icoId,false);}
  }

  btnWord.addEventListener("click",()=>exportar("docx","spinWord","icoWord",btnWord));
  btnPdf .addEventListener("click",()=>exportar("pdf", "spinPdf", "icoPdf", btnPdf));

  // ════════════════════════════════════════════
  // PLAYER
  // ════════════════════════════════════════════
  vol.addEventListener("input",()=>{audio.volume=Number(vol.value)/100;volLbl.textContent=`${vol.value}%`;});
  speed.addEventListener("change",()=>{audio.playbackRate=Number(speed.value);});

  // ════════════════════════════════════════════
  // ATAJOS DE TECLADO GLOBALES
  // ════════════════════════════════════════════
  function renderAtajos(modo) {
    state.modoAtajos=modo;localStorage.setItem("tx_shortcut_mode",modo);
    const btnES=document.getElementById("btnModeES"),btnCu=document.getElementById("btnModeCustom");
    if(btnES)btnES.classList.toggle("active",modo==="express");
    if(btnCu)btnCu.classList.toggle("active",modo==="custom");
    const atajos=MODOS[modo]||MODOS.express;
    keyMap={};atajos.forEach(a=>{const n=keyNativa(a.key);if(n)keyMap[n]=a.action;});
    if(scRows){scRows.innerHTML=atajos.map(a=>`<div class="sc-row"><span class="sc-icon"><i class="fa-solid ${a.icon}"></i></span><kbd class="sc-key" id="kbd-${a.key.replace(" ","_")}">${a.key}</kbd><span class="sc-action">${a.label}</span></div>`).join("");}
  }
  function keyNativa(l){return{"F4":"F4","F7":"F7","F9":"F9","F10":"F10","F11":"F11","Espacio":" "}[l]||null;}
  function ejecutarAccion(a){
    switch(a){
      case"playpause":audio.paused?audio.play():audio.pause();break;
      case"stop":audio.pause();audio.currentTime=0;break;
      case"rewind":audio.currentTime=Math.max(0,audio.currentTime-state.SEEK_SECS);break;
      case"forward":audio.currentTime=Math.min(audio.duration||0,audio.currentTime+state.SEEK_SECS);break;
      case"next":if(window.Toast)Toast.show("Función disponible próximamente","info");break;
    }
  }
  document.addEventListener("keydown",e=>{
    const enEditor=document.activeElement?.closest?.(".ck-editor__editable");
    const tag=document.activeElement?.tagName?.toLowerCase();
    if(tag==="input"||tag==="textarea"||tag==="select"||enEditor)return;
    if(dictOverlay.classList.contains("show"))return;
    const action=keyMap[e.key];
    if(action){
      e.preventDefault();ejecutarAccion(action);
      const label=Object.entries({"F4":"F4","F7":"F7","F9":"F9","F10":"F10","F11":"F11","Espacio":" "}).find(([,v])=>v===e.key)?.[0];
      if(label){const kbd=document.getElementById(`kbd-${label}`);if(kbd){kbd.classList.add("pressed");setTimeout(()=>kbd.classList.remove("pressed"),200);}}
    }
  });
  window.setMode=function(modo){renderAtajos(modo);};

  // ════════════════════════════════════════════
  // DICCIONARIO
  // ════════════════════════════════════════════
  btnDiccionario.addEventListener("click",()=>abrirDiccionario());
  dpClose.addEventListener("click",()=>cerrarDiccionario());
  dictOverlay.addEventListener("click",e=>{if(e.target===dictOverlay)cerrarDiccionario();});
  document.addEventListener("keydown",e=>{if(e.key==="Escape"&&dictOverlay.classList.contains("show"))cerrarDiccionario();});
  function abrirDiccionario(){dictOverlay.classList.add("show");document.body.style.overflow="hidden";if(!state.diccionario)cargarDiccionario();else renderizarDiccionario(state.diccionario,"");setTimeout(()=>dpSearchInput.focus(),300);}
  function cerrarDiccionario(){dictOverlay.classList.remove("show");document.body.style.overflow="";}

  async function cargarDiccionario() {
    try{
      const r=await fetch("/diccionario");const d=await r.json();
      state.diccionario=d;renderizarDiccionario(d,"");
      // Actualizar términos del AC con los del diccionario
      const terminos=[];
      const esp=d.especialidades||{};
      Object.values(esp).forEach(info=>{(info.terminos||[]).forEach(t=>{if(t&&!terminos.includes(t))terminos.push(t);});});
      if(window.actualizarTerminosAC)window.actualizarTerminosAC(terminos);
    }catch{dpList.innerHTML=`<div style="padding:20px;color:#ef4444;"><i class="fa-solid fa-circle-exclamation"></i> Error al cargar</div>`;}
  }

  function renderizarDiccionario(data,filtro){
    const fl=(filtro||"").toLowerCase();
    const labels={radiologia:{nombre:"Radiología",icon:"fa-x-ray"},anatomia:{nombre:"Anatomía",icon:"fa-bone"},procedimientos:{nombre:"Procedimientos",icon:"fa-stethoscope"},medicamentos:{nombre:"Medicamentos",icon:"fa-pills"},patologias:{nombre:"Patologías",icon:"fa-heart-pulse"},general:{nombre:"General",icon:"fa-book"},signos_alarma:{nombre:"Signos de alarma",icon:"fa-triangle-exclamation"},propios:{nombre:"Propios",icon:"fa-user-doctor"}};
    const esp=data.especialidades||{};const pc={};
    Object.entries(esp).forEach(([,info])=>{(info.terminos||[]).forEach(t=>{if(!pc.radiologia)pc.radiologia=[];pc.radiologia.push(t);});});
    let total=0,cats=0,matches=0,html="";
    for(const cat of Object.keys(pc)){
      let terminos=pc[cat];if(fl)terminos=terminos.filter(t=>t.toLowerCase().includes(fl));
      total+=pc[cat].length;if(!terminos.length)continue;cats++;matches+=terminos.length;
      const info=labels[cat]||{nombre:cat,icon:"fa-tag"};
      html+=`<div class="dp-cat"><div class="dp-cat-header" onclick="toggleCat(this)"><div class="dp-cat-left"><div class="dp-cat-icon cat-${cat}"><i class="fa-solid ${info.icon}"></i></div><span class="dp-cat-name">${info.nombre}</span></div><div style="display:flex;align-items:center;gap:8px;"><span class="dp-cat-count">${terminos.length}</span><i class="fa-solid fa-chevron-right dp-cat-arrow"></i></div></div><div class="dp-cat-terms">${terminos.map(t=>termTag(t,cat,fl)).join("")}</div></div>`;
    }
    dpTotalCount.textContent=total;dpCatCount.textContent=cats;
    dpSearchCount.textContent=fl?(matches>0?`${matches} resultado${matches!==1?"s":""}`:"Sin resultados"):"";
    if(html){dpList.innerHTML=html;dpEmpty.style.display="none";}else{dpList.innerHTML="";dpEmpty.style.display="block";}
  }

  function termTag(t,cat,fl){
    let label=t;
    if(fl){const i=t.toLowerCase().indexOf(fl);if(i>=0)label=t.substring(0,i)+`<mark style="background:rgba(124,58,237,.15);border-radius:3px;padding:0 2px;">`+t.substring(i,i+fl.length)+`</mark>`+t.substring(i+fl.length);}
    return `<span class="dp-term">${label}<button class="term-del" onclick="eliminarTermino('${t.replace(/'/g,"\\'")}',this)"><i class="fa-solid fa-xmark"></i></button></span>`;
  }
  window.toggleCat=function(h){h.classList.toggle("collapsed");};

  let searchTimer=null;
  dpSearchInput.addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{if(state.diccionario)renderizarDiccionario(state.diccionario,dpSearchInput.value);},200);});

  dpBtnAgregar.addEventListener("click",()=>agregarTermino());
  dpTermInput.addEventListener("keydown",e=>{if(e.key==="Enter")agregarTermino();});

  async function agregarTermino() {
    const t=dpTermInput.value.trim(),c=dpCatSelect.value;
    if(!t){dpTermInput.focus();dpTermInput.style.borderColor="rgba(239,68,68,.5)";setTimeout(()=>dpTermInput.style.borderColor="",1500);return;}
    dpBtnAgregar.disabled=true;
    try{
      const r=await fetch("/diccionario/agregar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({termino:t,categoria:c})});
      const d=await r.json();
      if(d.ok){
        if(d.agregado){
          toastPanel(`✓ "${t}" agregado`,"ok");dpTermInput.value="";
          await cargarDiccionario(); // recarga y actualiza AC automáticamente
        }else toastPanel(`⚠ "${t}" ya existe`,"warn");
      }
    }catch{toastPanel("Error al agregar","err");}
    finally{dpBtnAgregar.disabled=false;dpTermInput.focus();}
  }

  if(dpBtnCorreccion){
    dpBtnCorreccion.addEventListener("click",async()=>{
      const i=document.getElementById("dpIncorrecto").value.trim();
      const c=document.getElementById("dpCorrecto").value.trim();
      if(!i||!c){notifyErr("Campos requeridos","Ambos campos son obligatorios.");return;}
      try{
        const r=await fetch("/diccionario/correccion",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({incorrecto:i,correcto:c})});
        const d=await r.json();
        if(d.ok){notifyOk("Corrección agregada",`${i} → ${c}`);document.getElementById("dpIncorrecto").value="";document.getElementById("dpCorrecto").value="";}
        else notifyErr("Error",d.error||"No se pudo guardar");
      }catch(e){notifyErr("Error",e.message);}
    });
  }

  window.eliminarTermino=async function(termino,btnEl){
    const span=btnEl.closest(".dp-term");span.style.opacity="0.4";span.style.pointerEvents="none";
    try{
      const r=await fetch("/diccionario/eliminar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({termino})});
      const d=await r.json();
      if(d.ok&&d.eliminado){span.style.transform="scale(0.8)";span.style.transition="all .2s";setTimeout(async()=>{await cargarDiccionario();toastPanel(`✓ "${termino}" eliminado`,"ok");},200);}
      else{span.style.opacity="1";span.style.pointerEvents="auto";toastPanel("No se pudo eliminar","err");}
    }catch{span.style.opacity="1";span.style.pointerEvents="auto";toastPanel("Error","err");}
  };

  let toastTimer=null;
  function toastPanel(msg,tipo){const c={ok:"#16a34a",warn:"#d97706",err:"#dc2626"};dpToast.textContent=msg;dpToast.style.background=c[tipo]||"rgba(15,23,42,.92)";dpToast.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>dpToast.classList.remove("show"),2500);}

  // ════════════════════════════════════════════
  // HELPERS UI
  // ════════════════════════════════════════════
  function setPendiente(on){state.pendiente=on;pendingPill.style.display=on?"inline-flex":"none";savedPill.style.display=!on?"inline-flex":"none";}
  function mostrarProgreso(fase){
    jobStatusBar.classList.add("visible");jsbBar.classList.add("indeterminate");jsbTiempo.style.display="none";
    jsbIcon.className="fa-solid fa-circle-notch fa-spin";jsbIcon.style.color="";
    jsbTexto.textContent=fase==="pending"?"Enviando a la cola...":"Procesando transcripción con IA...";
    if(fase==="pending")document.getElementById("step-upload")?.classList.add("active");
    else{document.getElementById("step-upload")?.classList.add("done");document.getElementById("step-ffmpeg")?.classList.add("active");}
  }
  function mostrarError(msg){
    jobStatusBar.classList.add("visible");jsbBar.classList.remove("indeterminate");jsbBar.style.width="100%";jsbBar.style.background="#ef4444";
    jsbIcon.className="fa-solid fa-circle-exclamation";jsbIcon.style.color="#ef4444";jsbTexto.textContent=msg;
    ["step-upload","step-ffmpeg","step-stt","step-llm","step-done"].forEach(id=>{const el=document.getElementById(id);if(el)el.classList.add("error");});
  }
  function setLoading(btn,sId,iId,on){btn.disabled=on;const s=document.getElementById(sId);const i=document.getElementById(iId);if(s)s.style.display=on?"inline-block":"none";if(i)i.style.display=on?"none":"inline-block";}
  function notifyOk(t,m){window.Swal?Swal.fire({title:t,text:m,icon:"success"}):alert(`${t}: ${m}`);}
  function notifyErr(t,m){window.Swal?Swal.fire({title:t,text:m,icon:"error"}):alert(`ERROR — ${t}: ${m}`);}

})();