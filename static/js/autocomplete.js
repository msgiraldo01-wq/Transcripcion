/* static/js/autocomplete.js — VITACORE v5
 * ─────────────────────────────────────────────
 * Autocompletado clínico para CKEditor 5
 *
 * v5 — Reescrito para integrarse DENTRO del modelo de CKEditor,
 *       evitando los problemas de window.getSelection() y
 *       la captura de eventos que nunca llegaban.
 *
 * Estrategia:
 *   - Escuchar change:data del modelo de CKEditor (no keyup global)
 *   - Extraer el fragmento usando editor.model (no window.getSelection)
 *   - Insertar la sugerencia usando editor.model.change() (igual que antes)
 *   - El dropdown se posiciona usando getBoundingClientRect() del editable
 * ─────────────────────────────────────────────
 */
(function (window) {
  "use strict";

  // ── Estado global del AC ─────────────────────────────────────
  const AC = {
    terminos:      [],
    dropdown:      null,
    sugerencias:   [],
    indiceActivo:  -1,
    timer:         null,
    DEBOUNCE_MS:   180,
    MIN_CHARS:     3,
    MAX_SUGS:      8,
    editoresReg:   {},   // { bloque: editor }
    editorActivo:  null, // editor con foco actualmente
    fragActual:    "",   // fragmento que disparó las sugerencias
  };

  // ════════════════════════════════════════════
  // DROPDOWN
  // ════════════════════════════════════════════

  function crearDropdown() {
    const el = document.createElement("div");
    el.id = "ac-dropdown";
    Object.assign(el.style, {
      position:     "fixed",
      zIndex:       "99999",
      background:   "#fff",
      border:       "1px solid rgba(148,163,184,.28)",
      borderRadius: "12px",
      boxShadow:    "0 8px 32px rgba(2,6,23,.18)",
      minWidth:     "240px",
      maxWidth:     "440px",
      maxHeight:    "260px",
      overflowY:    "auto",
      display:      "none",
      padding:      "4px",
    });
    document.body.appendChild(el);

    // Cerrar al hacer click fuera
    document.addEventListener("mousedown", e => {
      if (!el.contains(e.target)) ocultarDropdown();
    });

    return el;
  }

  function ocultarDropdown() {
    if (AC.dropdown) AC.dropdown.style.display = "none";
    AC.sugerencias  = [];
    AC.indiceActivo = -1;
    AC.fragActual   = "";
  }

  function mostrarDropdown(sugerencias, frag) {
    if (!sugerencias.length) { ocultarDropdown(); return; }
    AC.sugerencias  = sugerencias;
    AC.indiceActivo = -1;
    AC.fragActual   = frag;

    const q = norm(frag);
    AC.dropdown.innerHTML = sugerencias.map((s, i) => {
      const sn  = norm(s);
      const idx = sn.indexOf(q);
      let label = esc(s);
      if (idx >= 0) {
        label = esc(s.slice(0, idx))
          + `<mark style="background:rgba(37,99,235,.15);border-radius:3px;padding:0 1px;font-weight:700;color:#1d4ed8;">`
          + esc(s.slice(idx, idx + frag.length))
          + `</mark>`
          + esc(s.slice(idx + frag.length));
      }
      return `<div class="ac-item" data-i="${i}" data-v="${esc(s)}"
        style="padding:8px 12px;border-radius:8px;cursor:pointer;font-size:13px;
               color:#0f172a;display:flex;align-items:center;gap:8px;transition:background .1s;">
        <i class="fa-solid fa-stethoscope" style="font-size:10px;color:#94a3b8;flex-shrink:0;"></i>
        <span>${label}</span>
      </div>`;
    }).join("")
    + `<div style="padding:5px 12px 4px;border-top:1px solid rgba(148,163,184,.12);
                   font-size:10px;color:#94a3b8;">
        <kbd style="background:#f1f5f9;border:1px solid rgba(148,163,184,.28);
                    border-radius:3px;padding:1px 5px;font-family:monospace;">Tab</kbd>
        · <kbd style="background:#f1f5f9;border:1px solid rgba(148,163,184,.28);
                       border-radius:3px;padding:1px 5px;font-family:monospace;">Enter</kbd>
        aceptar &nbsp;·&nbsp;
        <kbd style="background:#f1f5f9;border:1px solid rgba(148,163,184,.28);
                    border-radius:3px;padding:1px 5px;font-family:monospace;">Esc</kbd> cerrar
      </div>`;

    // Eventos de cada item
    AC.dropdown.querySelectorAll(".ac-item").forEach(item => {
      item.addEventListener("mouseenter", () => {
        limpiarActivo();
        item.style.background = "rgba(37,99,235,.06)";
        AC.indiceActivo = parseInt(item.dataset.i);
      });
      item.addEventListener("mouseleave", () => {
        item.style.background = "";
      });
      item.addEventListener("mousedown", e => {
        e.preventDefault();
        aceptarSugerencia(item.dataset.v);
      });
    });

    AC.dropdown.style.display = "block";
    posicionarDropdown();
  }

  function posicionarDropdown() {
    if (!AC.editorActivo) return;
    try {
      // Obtener posición del DOM root del editor activo
      const root = AC.editorActivo.editing.view.getDomRoot();
      if (!root) return;

      // Intentar posicionar cerca del cursor usando la selección nativa
      const sel = window.getSelection();
      if (sel && sel.rangeCount > 0) {
        const rects = sel.getRangeAt(0).getClientRects();
        if (rects.length > 0) {
          const r    = rects[rects.length - 1];
          let   top  = r.bottom + window.scrollY + 6;
          let   left = r.left   + window.scrollX;
          if (left + 300 > window.innerWidth - 16) left = window.innerWidth - 316;
          AC.dropdown.style.top  = Math.round(top)  + "px";
          AC.dropdown.style.left = Math.round(left) + "px";
          return;
        }
      }

      // Fallback: posicionar debajo del editor
      const rect = root.getBoundingClientRect();
      AC.dropdown.style.top  = Math.round(rect.bottom + window.scrollY + 6) + "px";
      AC.dropdown.style.left = Math.round(rect.left   + window.scrollX)     + "px";
    } catch (e) { /* silencioso */ }
  }

  function limpiarActivo() {
    AC.dropdown.querySelectorAll(".ac-item").forEach(i => i.style.background = "");
  }

  function marcarActivo(idx) {
    limpiarActivo();
    const items = AC.dropdown.querySelectorAll(".ac-item");
    if (idx >= 0 && idx < items.length) {
      items[idx].style.background = "rgba(37,99,235,.10)";
      items[idx].scrollIntoView({ block: "nearest" });
    }
  }

  // ════════════════════════════════════════════
  // EXTRACCIÓN DEL FRAGMENTO — VÍA MODELO CKEditor
  // ════════════════════════════════════════════

  function obtenerFragmentoDesdeEditor(editor) {
    try {
      const sel = editor.model.document.selection;
      const pos = sel.getFirstPosition();
      if (!pos) return "";
  
      // En CKEditor 5, el texto antes del cursor está en nodeBefore
      const nodo = pos.nodeBefore;
      if (!nodo || !nodo.data) return "";
  
      // nodeBefore.data contiene TODO el texto del nodo
      // tomamos la última palabra
      const match = nodo.data.match(/[\wáéíóúñüÁÉÍÓÚÑÜ][\wáéíóúñüÁÉÍÓÚÑÜ\s]*$/);
      return match ? match[0].trimStart() : "";
    } catch (e) {
      return "";
    }
  }
  // ════════════════════════════════════════════
  // BÚSQUEDA EN TÉRMINOS
  // ════════════════════════════════════════════

  function norm(s) {
    return s.toLowerCase()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .trim();
  }

  function esc(s) {
    return s.replace(/&/g,"&amp;")
            .replace(/</g,"&lt;")
            .replace(/>/g,"&gt;")
            .replace(/"/g,"&quot;");
  }

  function buscarSugerencias(frag) {
    if (!frag || frag.length < AC.MIN_CHARS) return [];
    const q  = norm(frag);
    const ex = AC.terminos.filter(t => norm(t).startsWith(q) && norm(t) !== q);
    const pa = AC.terminos.filter(t => norm(t).includes(q) && !norm(t).startsWith(q));
    return [...new Set([...ex, ...pa])].slice(0, AC.MAX_SUGS);
  }

  // ════════════════════════════════════════════
  // ACEPTAR SUGERENCIA — VÍA MODELO CKEditor
  // ════════════════════════════════════════════

  function aceptarSugerencia(termino) {
    if (!termino || !AC.editorActivo) { ocultarDropdown(); return; }
    const frag = AC.fragActual;

    try {
      AC.editorActivo.model.change(writer => {
        const sel = AC.editorActivo.model.document.selection;
        const pos = sel.getFirstPosition();
        if (!pos) return;

        // Borrar el fragmento escrito
        if (frag.length > 0 && pos.offset >= frag.length) {
          const desde = pos.getShiftedBy(-frag.length);
          const rango = writer.createRange(desde, pos);
          if (rango && !rango.isCollapsed) {
            writer.remove(rango);
          }
        }

        // Insertar el término + espacio
        const posFinal = AC.editorActivo.model.document.selection.getFirstPosition();
        writer.insertText(termino + " ", posFinal);
      });
    } catch (e) {
      console.warn("[AC] Error al insertar:", e.message);
    }

    ocultarDropdown();
    try { AC.editorActivo.editing.view.focus(); } catch (e) { /* silencioso */ }
  }

  // ════════════════════════════════════════════
  // INTEGRACIÓN CON CADA EDITOR
  // ════════════════════════════════════════════

  function integrarEditor(bloque, editor) {
    // Marcar editor activo cuando recibe foco
    editor.editing.view.document.on("focus", () => {
      AC.editorActivo = editor;
    });

    // Limpiar editor activo al perder foco
    editor.editing.view.document.on("blur", () => {
      // Delay para que el click en el dropdown no cierre todo
      setTimeout(() => {
        if (AC.editorActivo === editor) {
          AC.editorActivo = null;
          ocultarDropdown();
        }
      }, 200);
    });

    // Escuchar cambios en el contenido del editor
    editor.model.document.on("change:data", () => {
      if (AC.editorActivo !== editor) return;

      clearTimeout(AC.timer);
      AC.timer = setTimeout(() => {
        const frag = obtenerFragmentoDesdeEditor(editor);
        if (frag.length >= AC.MIN_CHARS) {
          const sugs = buscarSugerencias(frag);
          if (sugs.length) {
            mostrarDropdown(sugs, frag);
          } else {
            ocultarDropdown();
          }
        } else {
          ocultarDropdown();
        }
      }, AC.DEBOUNCE_MS);
    });

    // Interceptar Tab / Enter / Esc / flechas cuando el dropdown está visible
    // Usamos prioridad "high" para ganarle al handler de Tab de transcripcion.js
    editor.editing.view.document.on("keydown", (evt, data) => {
      if (AC.dropdown.style.display === "none") return;
      if (AC.editorActivo !== editor) return;

      switch (data.domEvent.key) {
        case "Escape":
          data.domEvent.preventDefault();
          evt.stop();
          ocultarDropdown();
          break;

        case "ArrowDown":
          data.domEvent.preventDefault();
          evt.stop();
          AC.indiceActivo = Math.min(AC.indiceActivo + 1, AC.sugerencias.length - 1);
          marcarActivo(AC.indiceActivo);
          break;

        case "ArrowUp":
          data.domEvent.preventDefault();
          evt.stop();
          AC.indiceActivo = Math.max(AC.indiceActivo - 1, 0);
          marcarActivo(AC.indiceActivo);
          break;

        case "Tab":
        case "Enter":
          if (AC.sugerencias.length) {
            data.domEvent.preventDefault();
            evt.stop();
            const idx = AC.indiceActivo >= 0 ? AC.indiceActivo : 0;
            aceptarSugerencia(AC.sugerencias[idx]);
          }
          break;
      }
    }, { priority: "high" });
  }

  // ════════════════════════════════════════════
  // API PÚBLICA
  // ════════════════════════════════════════════

  /**
   * Inicializar el autocompletado.
   * @param {Object} editors  - { bloque: CKEditorInstance }
   * @param {Array}  terminos - lista plana de strings
   */
  window.initAutocompletado = function (editors, terminos) {
    AC.terminos    = terminos || [];
    AC.editoresReg = editors  || {};

    if (!AC.dropdown) {
      AC.dropdown = crearDropdown();
    }

    // Integrar cada editor
    Object.entries(AC.editoresReg).forEach(([bloque, editor]) => {
      if (editor && typeof editor.model !== "undefined") {
        integrarEditor(bloque, editor);
      }
    });

    console.log(`[AC] v5 listo — ${AC.terminos.length} términos, ${Object.keys(AC.editoresReg).length} editores`);
  };

  /**
   * Actualizar la lista de términos en caliente
   * (se llama cuando el usuario agrega un término al diccionario).
   */
  window.actualizarTerminosAC = function (terminos) {
    AC.terminos = terminos || [];
    console.log(`[AC] Términos actualizados — ${AC.terminos.length}`);
  };

})(window);