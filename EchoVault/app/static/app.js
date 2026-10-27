(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    memories: [],
    health: null,
    activeMode: 'audio',
    pendingAudio: null,
    activeMemoryId: null,
    filter: 'all',
    search: '',
    isBusy: false,
  };

  const accentPalette = [
    { accent: '#bd654d', wash: '#f6e7df', icon: 'story' },
    { accent: '#7b8d73', wash: '#e8eee5', icon: 'leaf' },
    { accent: '#c59a4d', wash: '#f7eedb', icon: 'sun' },
    { accent: '#947e91', wash: '#f0eaf0', icon: 'book' },
  ];

  const icons = {
    story: '<svg viewBox="0 0 24 24"><path d="M5 4.5h10l4 4V20H5z"/><path d="M15 4.5V9h4M8 13h8M8 16h6"/></svg>',
    leaf: '<svg viewBox="0 0 24 24"><path d="M19.5 4.5C11 4 5 7.5 5 13c0 3.5 2.8 5.8 6 5.2 5.1-1 7.6-7.1 8.5-13.7Z"/><path d="M4 21c2.8-5.3 6.4-8.4 11-11"/></svg>',
    sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/></svg>',
    book: '<svg viewBox="0 0 24 24"><path d="M4 5.5A3.5 3.5 0 0 1 7.5 2H20v17H7.5A3.5 3.5 0 0 0 4 22z"/><path d="M4 5.5v13A3.5 3.5 0 0 1 7.5 15H20M8 6h7M8 9h7"/></svg>',
    audio: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/></svg>',
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[character]);
  }

  function formatDate(value, options = {}) {
    if (!value) return 'JUST ADDED';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'JUST ADDED';
    return new Intl.DateTimeFormat(undefined, options.long ? { year: 'numeric', month: 'long', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
  }

  function wordCount(text) {
    const trimmed = String(text || '').trim();
    return trimmed ? trimmed.split(/\s+/).length : 0;
  }

  function prettyCount(number) {
    return new Intl.NumberFormat(undefined, { notation: number > 9999 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(number);
  }

  async function api(path, options = {}) {
    const config = { ...options, headers: { ...(options.headers || {}) } };
    if (config.body && typeof config.body !== 'string' && !(config.body instanceof FormData)) {
      config.headers['Content-Type'] = 'application/json';
      config.body = JSON.stringify(config.body);
    }
    const response = await fetch(path, config);
    const type = response.headers.get('content-type') || '';
    const payload = type.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) {
      const message = typeof payload === 'object' && payload ? payload.detail || payload.message : payload;
      throw new Error(message || `Request failed (${response.status}).`);
    }
    return payload;
  }

  function toast(message, kind = 'success', duration = 3800) {
    const region = $('#toast-region');
    const item = document.createElement('div');
    item.className = `toast${kind === 'error' ? ' error' : ''}`;
    item.innerHTML = `<span class="toast-mark">${kind === 'error' ? '!' : '✓'}</span><span>${escapeHtml(message)}</span>`;
    region.appendChild(item);
    window.setTimeout(() => item.remove(), duration);
  }

  function setBusy(busy, label) {
    state.isBusy = busy;
    const save = $('#save-memory');
    const transcribe = $('#transcribe-button');
    if (save) {
      save.disabled = busy;
      save.innerHTML = busy ? '<span class="button-spinner"></span> Saving memory…' : `${inlineIcon('save')} Save to my archive`;
    }
    if (transcribe) {
      transcribe.disabled = busy || !$('#audio-file').files?.[0];
      transcribe.innerHTML = busy && label === 'transcribe' ? '<span class="button-spinner"></span> Transcribing…' : `${inlineIcon('upload')} Transcribe audio`;
    }
  }

  function inlineIcon(name) {
    if (name === 'save') return '<svg viewBox="0 0 24 24"><path d="M5 4h12l2 2v14H5zM8 4v6h8V4M8 20v-6h8v6"/></svg>';
    if (name === 'upload') return '<svg viewBox="0 0 24 24"><path d="M12 3v18M8 7l4-4 4 4M5 13v6h14v-6"/></svg>';
    return '';
  }

  function openModal(id) {
    closeSidebar();
    $$('.modal').forEach((modal) => { modal.hidden = modal.id !== id; });
    $('#modal-backdrop').hidden = false;
    document.body.style.overflow = 'hidden';
    const modal = $(`#${id}`);
    const focusTarget = modal?.querySelector('input:not([type="file"]), textarea, button');
    window.setTimeout(() => focusTarget?.focus({ preventScroll: true }), 50);
  }

  function closeModal() {
    const composerIsOpen = !$('#composer-modal').hidden;
    const stagedAudioId = composerIsOpen ? state.pendingAudio?.audio_file_id : null;
    if (stagedAudioId) void discardStagedAudio(stagedAudioId);
    if (composerIsOpen) state.pendingAudio = null;
    $$('.modal').forEach((modal) => { modal.hidden = true; });
    $('#modal-backdrop').hidden = true;
    document.body.style.overflow = '';
    state.activeMemoryId = null;
  }

  async function discardStagedAudio(fileId) {
    if (!fileId) return;
    try { await api(`/api/staged-audio/${encodeURIComponent(fileId)}`, { method: 'DELETE' }); }
    catch (error) { /* Safe to ignore if the server already discarded or attached this file. */ }
  }

  function openInfo(kind = 'privacy') {
    const modal = $('#info-modal');
    const title = $('#info-title');
    const eyebrow = $('#info-eyebrow');
    const content = $('#info-content');
    if (kind === 'how') {
      eyebrow.textContent = 'A SIMPLE, PRIVATE WORKFLOW';
      title.textContent = 'From voice to memory.';
      const aiStepCopy = state.health?.ai_provider === 'ollama'
        ? 'Local Gemma through Ollama creates a grounded summary. Audio, transcript, and archive data stay on this device.'
        : 'OpenRouter creates a grounded summary. Transcript text is sent for cloud inference; the original audio and archive stay local.';
      content.innerHTML = `
        <div class="info-list">
          <div class="info-row"><span>${icons.audio}</span><div><strong>01 · Bring in a story</strong><p>Upload an audio recording and transcribe it with Whisper, or paste a transcript yourself.</p></div></div>
          <div class="info-row"><span>${icons.story}</span><div><strong>02 · Make it easier to revisit</strong><p>${aiStepCopy}</p></div></div>
          <div class="info-row"><span>${icons.book}</span><div><strong>03 · Keep it searchable</strong><p>Save the memory in your local archive. Search, correct the transcript, export a Markdown copy, or delete the record.</p></div></div>
        </div>
        <p>EchoVault does not simulate a person's voice or claim to speak for someone. It preserves the source material and helps you find it again.</p>`;
    } else if (kind === 'about') {
      eyebrow.textContent = 'A LOCAL-FIRST MEMORY ARCHIVE';
      title.textContent = 'Meet EchoVault.';
      const provider = state.health?.ai_provider || 'openrouter';
      const ollama = state.health?.ollama;
      const openrouter = state.health?.openrouter;
      const modelMessage = provider === 'openrouter'
        ? (openrouter?.configured ? `${escapeHtml(openrouter.model)} is configured through OpenRouter.` : 'OpenRouter is selected, but OPENROUTER_API_KEY is missing.')
        : (ollama?.model_installed ? `${escapeHtml(ollama.model)} is available locally.` : 'Ollama/Gemma is not currently detected; text memories still save using a basic local summary.');
      const providerDescription = provider === 'openrouter'
        ? 'faster-whisper handles local speech recognition. OpenRouter handles AI summarization, so transcript text is sent to OpenRouter and the selected model provider.'
        : 'faster-whisper handles local speech recognition; Gemma via Ollama handles local summarization when configured.';
      const privacyDescription = provider === 'openrouter'
        ? 'Audio files and archive records remain on this device, but transcript text is sent to OpenRouter for summarization. Check the selected model/provider privacy terms before using sensitive stories.'
        : 'EchoVault does not send transcripts to a remote AI service in this configuration. Audio, transcript, and summaries are handled locally.';
      content.innerHTML = `
        <p>EchoVault is an open-AI memory archive built around user-controlled storage. It turns recordings and transcripts into editable, searchable records.</p>
        <div class="info-list">
          <div class="info-row"><span>${icons.story}</span><div><strong>Data lives here</strong><p>SQLite, recordings, and model caches live in the local EchoVault data directory.</p></div></div>
          <div class="info-row"><span>${icons.leaf}</span><div><strong>AI components</strong><p>${providerDescription}</p></div></div>
        </div>
        <div class="model-warning">Model status: ${modelMessage} ${privacyDescription}</div>`;
    } else {
      eyebrow.textContent = 'A NOTE ON PRIVACY';
      title.textContent = 'Your memories stay close.';
      const usesOpenRouter = state.health?.ai_provider === 'openrouter';
      const processingCopy = usesOpenRouter
        ? 'Audio transcription and archive storage stay on your device, but transcript text is sent to OpenRouter for summarization. Review the provider privacy terms before processing sensitive memories.'
        : 'Summarization uses local Ollama when available, and transcription uses faster-whisper on this machine. The app does not send transcripts to a remote AI provider in this configuration.';
      const modeCopy = usesOpenRouter
        ? 'OpenRouter cloud inference is enabled. Your API key is kept in the server-side .env file and is not sent to the browser.'
        : 'Local inference is enabled. After model files are downloaded, summarization and transcription can work without an internet connection.';
      content.innerHTML = `
        <p>EchoVault stores its memory archive and original audio locally. AI processing depends on the selected provider.</p>
        <div class="info-list">
          <div class="info-row"><span><svg viewBox="0 0 24 24"><path d="M12 3 19 6v5c0 5-3 8-7 10-4-2-7-5-7-10V6l7-3Z"/><path d="m9 12 2 2 4-4"/></svg></span><div><strong>Local storage</strong><p>Audio files and memory records are saved in the local data directory configured by ECHOVAULT_DATA_DIR.</p></div></div>
          <div class="info-row"><span><svg viewBox="0 0 24 24"><path d="M4 4h16v16H4zM8 8h8M8 12h8M8 16h5"/></svg></span><div><strong>AI processing</strong><p>${processingCopy} ${modeCopy}</p></div></div>
          <div class="info-row"><span><svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13M10 10v7M14 10v7"/></svg></span><div><strong>You control deletion</strong><p>Delete a memory from the detail view to remove its database record and associated audio file. Export important memories to keep a separate copy.</p></div></div>
        </div>
        <div class="model-warning">Keep your API key private. Never commit .env to GitHub. To use OpenRouter, make sure you understand that the transcript leaves your device for remote inference.</div>`;
    }
    openModal('info-modal');
  }

  function closeSidebar() { $('#sidebar').classList.remove('open'); }

  async function refreshHealth() {
    const pill = $('#connection-pill');
    const text = $('#connection-text');
    try {
      state.health = await api('/api/health');
      const provider = state.health.ai_provider || 'ollama';
      const llm = state.health.ollama;
      const openrouter = state.health.openrouter;
      const ready = Boolean(state.health.ai_ready);
      pill.classList.remove('ready', 'warn');
      if (provider === 'openrouter') {
        if (ready) {
          pill.classList.add('ready');
          text.textContent = `${openrouter?.model || 'OpenRouter'} · configured`;
        } else {
          pill.classList.add('warn');
          text.textContent = 'OpenRouter key missing';
        }
      } else if (llm?.available && llm?.model_installed) {
        pill.classList.add('ready');
        text.textContent = `${llm.model} · local`;
      } else {
        pill.classList.add('warn');
        text.textContent = 'Local archive ready';
      }
      const privacyCopy = document.querySelector('#privacy-inline-copy');
      if (privacyCopy) {
        privacyCopy.innerHTML = provider === 'openrouter'
          ? '<strong>Local archive, cloud AI.</strong> Audio transcription and storage stay local, but transcript text is sent to OpenRouter for summarization.'
          : '<strong>Private by default.</strong> Audio, transcripts, and summaries are stored locally. No upload to a remote AI service.';
      }
    } catch (error) {
      pill.classList.remove('ready');
      pill.classList.add('warn');
      text.textContent = 'Backend unavailable';
      toast('Could not reach EchoVault backend. Start the app with run_windows.bat or the README command.', 'error', 6500);
    }
  }

  async function loadMemories() {
    const grid = $('#memory-grid');
    grid.innerHTML = '<div class="loading-shelf"><span class="button-spinner"></span><span>Opening your archive…</span></div>';
    try {
      const payload = await api('/api/memories');
      state.memories = payload.items || [];
      renderMemories();
      updateStats();
    } catch (error) {
      grid.innerHTML = `<div class="backend-error"><strong>Could not open the archive.</strong><p>${escapeHtml(error.message)}</p><button class="button button-outline" id="retry-load" type="button">Try again</button></div>`;
      $('#retry-load')?.addEventListener('click', loadMemories);
    }
  }

  function getVisibleMemories() {
    const query = state.search.trim().toLocaleLowerCase();
    return state.memories.filter((memory) => {
      const matchFilter = state.filter !== 'audio' || Boolean(memory.audio_file_id);
      const searchable = [memory.title, memory.summary, memory.transcript, ...(memory.people || []), ...(memory.places || []), ...(memory.themes || [])].join(' ').toLocaleLowerCase();
      return matchFilter && (!query || searchable.includes(query));
    });
  }

  function updateStats() {
    const count = state.memories.length;
    const words = state.memories.reduce((sum, item) => sum + wordCount(item.transcript), 0);
    const audio = state.memories.filter((item) => item.audio_file_id).length;
    $('#stat-memories').textContent = prettyCount(count);
    $('#stat-words').textContent = prettyCount(words);
    $('#stat-audio').textContent = prettyCount(audio);
    const provider = state.health?.ai_provider || 'openrouter';
    $('#stat-health').textContent = state.health?.ai_ready ? 'AI Ready' : (provider === 'openrouter' ? 'Key Missing' : 'AI Offline');
    $('#nav-count').textContent = count > 99 ? '99+' : String(count);
    $('#heading-count').textContent = count > 99 ? '99+' : String(count);
  }

  function renderMemories() {
    const grid = $('#memory-grid');
    const empty = $('#empty-state');
    const noResults = $('#no-results');
    const items = getVisibleMemories();
    grid.innerHTML = '';
    const trulyEmpty = state.memories.length === 0 && !state.search && state.filter === 'all';
    empty.hidden = !trulyEmpty;
    noResults.hidden = items.length > 0 || trulyEmpty;
    if (!items.length) return;
    grid.innerHTML = items.map((memory) => {
      const index = Math.abs(hashCode(memory.id || memory.title)) % accentPalette.length;
      const palette = accentPalette[index];
      const tags = [...(memory.themes || []), ...(memory.people || [])].filter(Boolean).slice(0, 2);
      const tagMarkup = tags.map((tag) => `<span class="memory-tag">${escapeHtml(tag)}</span>`).join('') || `<span class="memory-tag">${memory.summary_source && memory.summary_source !== 'local-extractive' ? 'AI summary' : 'Saved story'}</span>`;
      const audioLabel = memory.audio_file_id ? `<span class="memory-meta">${icons.audio} Audio</span>` : `<span class="memory-meta">${icons.story} Text</span>`;
      return `<article class="memory-card" data-memory-id="${escapeHtml(memory.id)}" style="--card-accent:${palette.accent};--card-wash:${palette.wash}" tabindex="0" role="button" aria-label="Open ${escapeHtml(memory.title)}">
        <div class="memory-card-top"><span class="memory-type-icon">${memory.audio_file_id ? icons.audio : icons[palette.icon]}</span><button class="memory-menu" type="button" data-open-memory="${escapeHtml(memory.id)}" aria-label="Open memory options">···</button></div>
        <div class="memory-date">${escapeHtml(formatDate(memory.created_at))}</div>
        <h3 class="memory-title">${escapeHtml(memory.title)}</h3>
        <p class="memory-summary">${escapeHtml(memory.summary || memory.transcript || 'No summary yet.')}</p>
        <div class="memory-card-bottom"><div class="memory-tags">${tagMarkup}</div>${audioLabel}</div>
      </article>`;
    }).join('');
    $$('.memory-card', grid).forEach((card) => {
      card.addEventListener('click', () => openMemory(card.dataset.memoryId));
      card.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openMemory(card.dataset.memoryId); }
      });
    });
    $$('[data-open-memory]', grid).forEach((button) => button.addEventListener('click', (event) => {
      event.stopPropagation(); openMemory(button.dataset.openMemory);
    }));
  }

  function hashCode(value) {
    let hash = 0;
    for (const character of String(value)) hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
    return hash;
  }

  function showComposer(mode = 'audio') {
    resetComposer();
    setComposerMode(mode);
    openModal('composer-modal');
  }

  function resetComposer() {
    $('#memory-form').reset();
    $('#form-error').hidden = true;
    $('#form-error').textContent = '';
    $('#audio-selected').hidden = true;
    $('#dropzone').hidden = false;
    $('#transcript-panel').hidden = true;
    $('#transcript-review').value = '';
    $('#transcript-meta').textContent = '';
    $('#audio-file').value = '';
    $('#selected-filename').textContent = '';
    $('#selected-filesize').textContent = '';
    $('#dropzone-title').textContent = 'Choose a recording to transcribe';
    $('#dropzone-subtitle').textContent = 'WAV, MP3, M4A, OGG or FLAC · up to 100 MB';
    if (state.pendingAudio?.audio_file_id) void discardStagedAudio(state.pendingAudio.audio_file_id);
    state.pendingAudio = null;
    state.isBusy = false;
    $('#save-memory').disabled = false;
    $('#transcribe-button').disabled = true;
    $('#save-memory').innerHTML = `${inlineIcon('save')} Save to my archive`;
    $('#transcribe-button').innerHTML = `${inlineIcon('upload')} Transcribe audio`;
  }

  function setComposerMode(mode) {
    state.activeMode = mode;
    $$('.composer-tab').forEach((tab) => {
      const selected = tab.dataset.mode === mode;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', selected ? 'true' : 'false');
    });
    $('#audio-panel').hidden = mode !== 'audio';
    $('#text-panel').hidden = mode !== 'text';
    if (mode === 'text') $('#memory-transcript').focus({ preventScroll: true });
  }

  function showFormError(message) {
    const error = $('#form-error');
    error.textContent = message;
    error.hidden = false;
  }

  function clearFormError() { $('#form-error').hidden = true; $('#form-error').textContent = ''; }

  async function transcribeSelectedAudio() {
    clearFormError();
    const file = $('#audio-file').files?.[0];
    if (!file || state.isBusy) return;
    if (!$('#memory-title').value.trim()) {
      $('#memory-title').value = file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
    }
    setBusy(true, 'transcribe');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const result = await api('/api/transcribe', { method: 'POST', body: formData });
      state.pendingAudio = { audio_file_id: result.audio_file_id, source_filename: result.source_filename };
      $('#transcript-review').value = result.transcript;
      $('#transcript-panel').hidden = false;
      const details = [];
      if (result.language) details.push(`Language: ${result.language}`);
      if (result.duration_seconds) details.push(`${Math.round(result.duration_seconds / 60 * 10) / 10} min audio`);
      details.push(`${wordCount(result.transcript)} words detected`);
      $('#transcript-meta').textContent = details.join(' · ');
      $('#transcript-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      toast('Transcript ready. Review it before saving.');
    } catch (error) {
      showFormError(error.message || 'Could not transcribe this recording.');
    } finally {
      setBusy(false);
    }
  }

  async function saveMemory(event) {
    event.preventDefault();
    clearFormError();
    if (state.isBusy) return;
    const title = $('#memory-title').value.trim();
    const transcript = state.activeMode === 'text' ? $('#memory-transcript').value.trim() : $('#transcript-review').value.trim();
    if (!title) return showFormError('Give this story a title before saving.');
    if (state.activeMode === 'audio' && !transcript) {
      return showFormError('Choose an audio file and transcribe it, or switch to “Paste transcript” to enter the words yourself.');
    }
    if (state.activeMode === 'text' && transcript.length < 5) return showFormError('Add at least a few words so the memory can be saved.');
    setBusy(true, 'save');
    try {
      const payload = {
        title,
        transcript,
        audio_file_id: state.activeMode === 'audio' ? state.pendingAudio?.audio_file_id || null : null,
        source_filename: state.activeMode === 'audio' ? state.pendingAudio?.source_filename || null : null,
      };
      const memory = await api('/api/memories', { method: 'POST', body: payload });
      const wasAudioSaved = state.activeMode === 'audio' && Boolean(payload.audio_file_id);
      const stagedId = state.pendingAudio?.audio_file_id;
      state.pendingAudio = null;
      if (stagedId && !wasAudioSaved) void discardStagedAudio(stagedId);
      closeModal();
      state.memories.unshift(memory);
      state.memories = state.memories.filter((item, index, list) => list.findIndex((candidate) => candidate.id === item.id) === index);
      state.search = '';
      $('#search-input').value = '';
      state.filter = 'all';
      $$('.filter-button').forEach((button) => button.classList.toggle('selected', button.dataset.filter === 'all'));
      renderMemories();
      updateStats();
      toast(memory.summary_source === 'local-extractive' ? 'Memory saved with a basic local summary. Check the backend terminal if AI summarization was expected.' : `Memory preserved with ${memory.summary_source}.`);
      if (state.health && (!state.health.ollama?.available || !state.health.ollama?.model_installed)) {
        // Keep status pill truthful; the local archive is still usable without a language model.
      }
    } catch (error) {
      showFormError(error.message || 'Could not save the memory.');
    } finally {
      setBusy(false);
    }
  }

  async function openMemory(memoryId) {
    try {
      const memory = state.memories.find((item) => item.id === memoryId) || await api(`/api/memories/${encodeURIComponent(memoryId)}`);
      state.activeMemoryId = memory.id;
      renderMemoryDetail(memory);
      openModal('detail-modal');
    } catch (error) {
      toast(error.message || 'Could not open this memory.', 'error');
    }
  }

  function renderMemoryDetail(memory, editMode = false) {
    const palette = accentPalette[Math.abs(hashCode(memory.id || memory.title)) % accentPalette.length];
    const details = [
      ['People', memory.people], ['Places', memory.places], ['Dates remembered', memory.dates], ['Themes', memory.themes],
    ].filter(([, list]) => Array.isArray(list) && list.length);
    const detailBlocks = details.map(([label, list]) => `<section class="detail-section"><div class="detail-section-heading"><strong>${escapeHtml(label.toUpperCase())}</strong></div><div class="detail-pills">${list.map((item) => `<span class="detail-pill">${escapeHtml(item)}</span>`).join('')}</div></section>`).join('');
    const quotes = (memory.quotes || []).length ? `<section class="detail-section"><div class="detail-section-heading"><strong>WORDS WORTH KEEPING</strong></div>${memory.quotes.map((quote) => `<blockquote class="detail-quote">“${escapeHtml(quote)}”</blockquote>`).join('')}</section>` : '';
    const audioBlock = memory.audio_file_id ? `<div class="detail-audio">${icons.audio}<div><strong>Original recording</strong><small>${escapeHtml(memory.source_filename || 'Saved audio')}</small></div><audio controls preload="none" src="/api/memories/${encodeURIComponent(memory.id)}/audio"></audio></div>` : '';
    const mainContent = editMode ? `
      <div class="detail-date">${escapeHtml(formatDate(memory.created_at, { long: true }))}</div>
      <form id="edit-memory-form" class="edit-memory-form"><label class="form-label" for="edit-title">STORY TITLE</label><input class="form-input" id="edit-title" name="title" maxlength="120" value="${escapeHtml(memory.title)}" required /><label class="form-label" for="edit-transcript">TRANSCRIPT / STORY TEXT</label><textarea class="form-textarea" id="edit-transcript" rows="10" required>${escapeHtml(memory.transcript)}</textarea><p class="input-note">Saving transcript edits regenerates the summary and extracted details with your selected AI provider, if available.</p><div class="modal-actions"><button type="button" class="button button-quiet" id="cancel-edit">Cancel</button><button type="submit" class="button button-primary" id="save-edit">Save changes</button></div></form>` : `
      <div class="detail-date">${escapeHtml(formatDate(memory.created_at, { long: true }))}</div>
      <h2 class="detail-title" id="detail-title" style="--card-accent:${palette.accent}">${escapeHtml(memory.title)}</h2>
      <div class="detail-summary-box"><div class="summary-label">${icons.sun} THE STORY, AT A GLANCE</div><p>${escapeHtml(memory.summary || 'A summary has not been created yet.')}</p><div class="source-note">Summary source: ${escapeHtml(memory.summary_source || 'local')} · Verify important details against the transcript.</div></div>
      ${audioBlock}
      <section class="detail-section"><div class="detail-section-heading"><strong>ORIGINAL TRANSCRIPT</strong><button type="button" id="edit-transcript-button">Edit transcript</button></div><div class="detail-transcript">${escapeHtml(memory.transcript)}</div></section>
      ${detailBlocks}${quotes}
      <div class="detail-actions"><button class="button button-outline" id="export-memory" type="button">↓ Export Markdown</button><button class="button button-outline" id="edit-memory" type="button">Edit memory</button><span class="spacer"></span><button class="button button-danger" id="delete-memory" type="button">Delete memory</button></div>`;
    $('#detail-content').innerHTML = mainContent;
    if (editMode) {
      $('#cancel-edit').addEventListener('click', () => renderMemoryDetail(memory));
      $('#edit-memory-form').addEventListener('submit', (event) => saveMemoryEdits(event, memory));
      return;
    }
    $('#export-memory').addEventListener('click', () => exportMemory(memory));
    $('#edit-memory').addEventListener('click', () => renderMemoryDetail(memory, true));
    $('#edit-transcript-button').addEventListener('click', () => renderMemoryDetail(memory, true));
    $('#delete-memory').addEventListener('click', () => deleteMemory(memory));
  }

  async function saveMemoryEdits(event, memory) {
    event.preventDefault();
    const title = $('#edit-title').value.trim();
    const transcript = $('#edit-transcript').value.trim();
    const submit = $('#save-edit');
    if (!title || transcript.length < 5) return toast('A title and a transcript of at least five characters are required.', 'error');
    submit.disabled = true;
    submit.textContent = 'Saving changes…';
    try {
      const updated = await api(`/api/memories/${encodeURIComponent(memory.id)}`, { method: 'PATCH', body: { title, transcript } });
      state.memories = state.memories.map((item) => item.id === updated.id ? updated : item);
      renderMemoryDetail(updated);
      renderMemories();
      updateStats();
      toast('Memory updated. Summary refreshed.');
    } catch (error) {
      toast(error.message || 'Could not update this memory.', 'error');
      submit.disabled = false;
      submit.textContent = 'Save changes';
    }
  }

  async function deleteMemory(memory) {
    const accepted = window.confirm(`Delete “${memory.title}”? This removes the memory record and its associated audio from this device.`);
    if (!accepted) return;
    try {
      await api(`/api/memories/${encodeURIComponent(memory.id)}`, { method: 'DELETE' });
      state.memories = state.memories.filter((item) => item.id !== memory.id);
      closeModal();
      renderMemories();
      updateStats();
      toast('Memory and associated audio deleted from this device.');
    } catch (error) {
      toast(error.message || 'Could not delete this memory.', 'error');
    }
  }

  function exportMemory(memory) {
    const lines = [
      `# ${memory.title}`, '',
      `- Saved: ${formatDate(memory.created_at, { long: true })}`,
      `- Summary source: ${memory.summary_source || 'local'}`,
      memory.source_filename ? `- Original audio: ${memory.source_filename}` : null,
      '', '## Summary', '', memory.summary || '', '', '## Transcript', '', memory.transcript || '',
    ].filter((line) => line !== null);
    if (memory.people?.length) lines.push('', '## People mentioned', '', ...memory.people.map((item) => `- ${item}`));
    if (memory.places?.length) lines.push('', '## Places mentioned', '', ...memory.places.map((item) => `- ${item}`));
    if (memory.dates?.length) lines.push('', '## Dates remembered', '', ...memory.dates.map((item) => `- ${item}`));
    if (memory.themes?.length) lines.push('', '## Themes', '', ...memory.themes.map((item) => `- ${item}`));
    if (memory.quotes?.length) lines.push('', '## Quotes', '', ...memory.quotes.map((item) => `> ${item}`));
    lines.push('', '---', 'Exported from EchoVault. Review the original transcript for important details.', '');
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${slugify(memory.title) || 'memory'}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast('Markdown export prepared.');
  }

  function slugify(value) { return String(value).toLocaleLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 70); }

  function useSample() {
    showComposer('text');
    $('#memory-title').value = 'The marigold courtyard (fictional sample)';
    $('#memory-transcript').value = 'When I was little, the courtyard behind our old house filled with marigolds every autumn. My grandmother would save the seeds in a small paper envelope and ask me to help her plant them after the first rain. She said a garden was a way of leaving a little colour for someone you might never meet. We would sit beside the green gate in the late afternoon and listen to the neighbours preparing for the festival. I still remember the smell of wet earth and the sound of her bangles as she watered the pots. This is a fictional sample story created to demonstrate EchoVault, not a real family recording.';
    $('#memory-transcript').focus({ preventScroll: true });
  }

  function onAudioFileSelected(file) {
    if (!file) return;
    clearFormError();
    if (file.size > 100 * 1024 * 1024) {
      showFormError('This file is larger than 100 MB. Choose a smaller recording.');
      $('#audio-file').value = '';
      return;
    }
    $('#audio-selected').hidden = false;
    $('#dropzone').hidden = true;
    $('#selected-filename').textContent = file.name;
    $('#selected-filesize').textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB · selected on this device`;
    $('#transcribe-button').disabled = false;
    if (!$('#memory-title').value.trim()) $('#memory-title').value = file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
  }

  async function testAiConnection() {
    const button = $('#test-ai-button');
    if (!button || button.disabled) return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = 'Testing…';
    try {
      const result = await api('/api/ai/test', { method: 'POST' });
      toast(`${result.provider} connected · ${result.model}`, 'success', 5200);
      await refreshHealth();
    } catch (error) {
      toast(error.message || 'AI connection test failed.', 'error', 7500);
    } finally {
      button.disabled = false;
      button.textContent = original || 'Test AI';
    }
  }

  function wireEvents() {
    $$('[data-action="create"]').forEach((button) => button.addEventListener('click', () => showComposer('audio')));
    $$('[data-action="privacy"]').forEach((button) => button.addEventListener('click', () => openInfo('privacy')));
    $$('[data-close-modal]').forEach((button) => button.addEventListener('click', closeModal));
    $('#modal-backdrop').addEventListener('click', closeModal);
    $('#how-it-works').addEventListener('click', () => openInfo('how'));
    $('#about-button').addEventListener('click', () => openInfo('about'));
    $('#test-ai-button')?.addEventListener('click', testAiConnection);
    $('#footer-privacy').addEventListener('click', () => openInfo('privacy'));
    $('#sample-button').addEventListener('click', useSample);
    $('#memory-form').addEventListener('submit', saveMemory);
    $('#transcribe-button').addEventListener('click', transcribeSelectedAudio);
    $('#audio-file').addEventListener('change', (event) => onAudioFileSelected(event.target.files?.[0]));
    $('#remove-audio').addEventListener('click', () => {
      if (state.pendingAudio?.audio_file_id) void discardStagedAudio(state.pendingAudio.audio_file_id);
      $('#audio-file').value = '';
      $('#audio-selected').hidden = true;
      $('#dropzone').hidden = false;
      $('#transcribe-button').disabled = true;
      state.pendingAudio = null;
      $('#transcript-panel').hidden = true;
      $('#transcript-review').value = '';
      clearFormError();
    });
    $$('.composer-tab').forEach((tab) => tab.addEventListener('click', () => setComposerMode(tab.dataset.mode)));
    $('#search-input').addEventListener('input', (event) => {
      state.search = event.target.value;
      renderMemories();
    });
    $$('.filter-button').forEach((button) => button.addEventListener('click', () => {
      state.filter = button.dataset.filter;
      $$('.filter-button').forEach((item) => item.classList.toggle('selected', item === button));
      renderMemories();
    }));
    $('#clear-search').addEventListener('click', () => {
      state.search = '';
      state.filter = 'all';
      $('#search-input').value = '';
      $$('.filter-button').forEach((button) => button.classList.toggle('selected', button.dataset.filter === 'all'));
      renderMemories();
    });
    $('#mobile-menu').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
    document.addEventListener('click', (event) => {
      if (window.innerWidth <= 940 && $('#sidebar').classList.contains('open') && !$('#sidebar').contains(event.target) && !$('#mobile-menu').contains(event.target)) closeSidebar();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        if (!$('#modal-backdrop').hidden) closeModal();
        closeSidebar();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        $('#search-input').focus();
      }
    });
    const dropzone = $('#dropzone');
    ['dragenter', 'dragover'].forEach((eventName) => dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.add('drag-over'); }));
    ['dragleave', 'drop'].forEach((eventName) => dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.remove('drag-over'); }));
    dropzone.addEventListener('drop', (event) => {
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;
      const input = $('#audio-file');
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      onAudioFileSelected(file);
    });
  }

  async function init() {
    wireEvents();
    await Promise.all([refreshHealth(), loadMemories()]);
    updateStats();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
