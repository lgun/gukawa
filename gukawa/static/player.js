(() => {
  'use strict';
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const clock = value => {
    const seconds = Math.max(0, Math.floor(Number.isFinite(value) ? value : 0));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  };

  function enhance(shell) {
    const video = shell.querySelector('video');
    const trackElements = Array.from(shell.querySelectorAll('[data-korean-track]'));
    let trackElement = trackElements.find(element => element.default) || trackElements[0];
    // Preserve the browser's normal player until a separate Korean file exists.
    if (!video || !trackElement) return;
    let track = trackElement.track;
    const translationSelect = shell.querySelector('[data-translation-select]');
    const frame = shell.querySelector('.video-frame');
    const layer = shell.querySelector('[data-subtitle-layer]');
    const text = shell.querySelector('[data-subtitle-text]');
    const controls = shell.querySelector('[data-player-controls]');
    const settings = shell.querySelector('[data-caption-settings]');
    const play = shell.querySelector('[data-play]');
    const seek = shell.querySelector('[data-seek]');
    const time = shell.querySelector('[data-time]');
    const mute = shell.querySelector('[data-mute]');
    const fullscreen = shell.querySelector('[data-fullscreen]');
    const koreanEnabled = shell.querySelector('[data-korean-enabled]');
    const status = shell.querySelector('[data-player-status]');
    const bottom = clamp(Number(shell.dataset.captionBottom) || 1, .03, 1);
    let loaded = false;
    let captionFailed = false;
    let dragging = false;
    let resizePending = false;
    let disposed = false;
    let previousCaption = null;

    function isFullscreen() {
      return document.fullscreenElement === shell || document.webkitFullscreenElement === shell || shell.classList.contains('is-faux-fullscreen');
    }
    function fitText() {
      let size = clamp(Math.round(frame.clientWidth * .036), 14, 36);
      text.style.fontSize = `${size}px`;
      const available = Math.max(14, frame.clientHeight * .35);
      while (size > 12 && text.scrollHeight > available) {
        text.style.fontSize = `${--size}px`;
      }
    }
    function captionText(cue) {
      return typeof cue.getCueAsHTML === 'function' ? cue.getCueAsHTML().textContent : cue.text;
    }
    function render() {
      const enabled = loaded && koreanEnabled.checked;
      layer.style.bottom = `${(1 - bottom) * 100}%`;
      const current = video.currentTime;
      // Reading by timestamp also handles paused seeks and browsers that delay activeCues.
      const cues = enabled ? Array.from(track.cues || []).filter(cue => cue.startTime <= current && current < cue.endTime) : [];
      const content = cues.map(captionText).join('\n');
      layer.hidden = !content.trim();
      if (content !== previousCaption) {
        text.replaceChildren();
        for (const [index, line] of content.split('\n').entries()) {
          if (index) text.append(document.createElement('br'));
          const span = document.createElement('span');
          span.className = 'subtitle-line';
          span.textContent = line;
          text.append(span);
        }
        previousCaption = content;
      }
      if (enabled) fitText();
    }
    function fit() {
      if (disposed) return;
      const style = getComputedStyle(shell);
      const paddingX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
      const paddingY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      const ratio = video.videoWidth && video.videoHeight ? video.videoWidth / video.videoHeight : 16 / 9;
      const width = Math.max(1, shell.clientWidth - paddingX);
      const maxHeight = isFullscreen()
        ? Math.max(60, shell.clientHeight - paddingY - controls.offsetHeight - settings.offsetHeight - status.offsetHeight)
        : Math.min(640, window.innerHeight * .72);
      const fittedWidth = Math.min(width, maxHeight * ratio);
      frame.style.width = `${fittedWidth}px`;
      frame.style.height = `${fittedWidth / ratio}px`;
      fullscreen.setAttribute('aria-label', isFullscreen() ? '전체 화면 끝내기' : '전체 화면');
      fullscreen.setAttribute('aria-pressed', String(isFullscreen()));
      render();
    }
    function scheduleFit() {
      if (resizePending) return;
      resizePending = true;
      requestAnimationFrame(() => { resizePending = false; fit(); });
    }
    function updateTime() {
      if (Number.isFinite(video.duration)) seek.max = String(video.duration);
      if (!dragging) seek.value = String(video.currentTime);
      time.textContent = `${clock(video.currentTime)} / ${clock(video.duration)}`;
      seek.setAttribute('aria-valuetext', time.textContent);
      play.textContent = video.paused ? '▶' : '❚❚';
      play.setAttribute('aria-label', video.paused ? '재생' : '일시정지');
      shell.classList.toggle('is-paused', video.paused);
      render();
    }
    async function togglePlay() {
      if (video.paused) {
        try { await video.play(); if (!captionFailed) status.textContent = ''; }
        catch { status.textContent = '재생 버튼을 다시 눌러 주세요.'; }
      } else video.pause();
      updateTime();
    }
    function toggleMute() {
      video.muted = !video.muted;
    }
    function fauxFullscreen(enabled) {
      shell.classList.toggle('is-faux-fullscreen', enabled);
      document.body.classList.toggle('player-page-locked', enabled);
      scheduleFit();
    }
    // When the <video> itself goes fullscreen (iPhone, Android rotate-to-fullscreen, browser menus),
    // the page overlay is not drawn, so show the same Korean file as a native subtitle track instead.
    const nativeKorean = shell.querySelector('[data-native-korean]')?.track;
    function videoIsNativeFullscreen() {
      return document.fullscreenElement === video || document.webkitFullscreenElement === video || Boolean(video.webkitDisplayingFullscreen);
    }
    function syncNativeKorean(force) {
      if (!nativeKorean) return;
      const show = (force ?? videoIsNativeFullscreen()) && koreanEnabled.checked && !koreanEnabled.disabled;
      nativeKorean.mode = show ? 'showing' : 'disabled';
    }
    async function toggleFullscreen() {
      if (shell.classList.contains('is-faux-fullscreen')) return fauxFullscreen(false);
      if (document.fullscreenElement === shell) return document.exitFullscreen();
      if (document.webkitFullscreenElement === shell) return document.webkitExitFullscreen();
      try {
        if (shell.requestFullscreen) await shell.requestFullscreen();
        else if (shell.webkitRequestFullscreen) shell.webkitRequestFullscreen();
        else if (video.webkitEnterFullscreen) { syncNativeKorean(true); video.webkitEnterFullscreen(); }
        else fauxFullscreen(true);
      } catch { fauxFullscreen(true); }
      scheduleFit();
    }
    function goTo(url, autoplay = !video.paused) {
      const target = new URL(url, location.href);
      if (autoplay) target.searchParams.set('autoplay', '1');
      if (isFullscreen()) target.searchParams.set('fullscreen', '1');
      location.href = target.pathname + target.search;
    }
    const endCard = shell.querySelector('[data-end-card]');
    const countdown = shell.querySelector('[data-countdown]');
    const autoNext = shell.querySelector('[data-autonext]');
    let countdownTimer = null;
    function stopCountdown() {
      if (countdownTimer) clearInterval(countdownTimer);
      countdownTimer = null;
      if (countdown) countdown.textContent = '';
    }
    function showEndCard() {
      if (!endCard) return;
      endCard.hidden = false;
      if (!shell.dataset.nextUrl || !autoNext || !autoNext.checked) return;
      let left = 5;
      countdown.textContent = `${left}초 뒤 자동으로 넘어가요.`;
      countdownTimer = setInterval(() => {
        left -= 1;
        if (left <= 0) { stopCountdown(); goTo(shell.dataset.nextUrl, true); return; }
        countdown.textContent = `${left}초 뒤 자동으로 넘어가요.`;
      }, 1000);
    }
    function hideEndCard() {
      stopCountdown();
      if (endCard) endCard.hidden = true;
    }
    if (autoNext) {
      try { autoNext.checked = localStorage.getItem('gukawa-autonext') !== 'off'; } catch {}
      autoNext.addEventListener('change', () => {
        try { localStorage.setItem('gukawa-autonext', autoNext.checked ? 'on' : 'off'); } catch {}
        if (!autoNext.checked) stopCountdown();
      });
    }
    shell.querySelector('[data-replay]')?.addEventListener('click', () => {
      hideEndCard();
      video.currentTime = 0;
      togglePlay();
    });
    for (const link of shell.querySelectorAll('[data-prev], [data-next], [data-next-link]')) {
      link.addEventListener('click', event => { event.preventDefault(); goTo(link.getAttribute('href'), link.hasAttribute('data-next-link') || !video.paused); });
    }
    video.addEventListener('ended', showEndCard);
    video.addEventListener('play', hideEndCard);
    video.addEventListener('seeking', () => { if (video.currentTime < video.duration - .5) hideEndCard(); });

    function trackFailed() {
      loaded = false;
      captionFailed = true;
      koreanEnabled.disabled = true;
      koreanEnabled.checked = false;
      status.textContent = '한국어 자막을 불러오지 못했어요. 영상은 그대로 재생할 수 있어요.';
      render();
      scheduleFit();
    }
    function trackReady() {
      loaded = Boolean(track.cues && track.cues.length);
      if (!loaded) return trackFailed();
      captionFailed = false;
      koreanEnabled.disabled = false;
      status.textContent = '';
      render();
      scheduleFit();
    }

    controls.hidden = false;
    settings.hidden = false;
    shell.classList.add('is-enhanced');
    video.controls = false;
    video.playsInline = true;
    video.disablePictureInPicture = true;
    koreanEnabled.disabled = true;
    play.addEventListener('click', togglePlay);
    video.addEventListener('click', togglePlay);
    video.addEventListener('dblclick', toggleFullscreen);
    for (const name of ['fullscreenchange', 'webkitfullscreenchange']) document.addEventListener(name, () => syncNativeKorean());
    video.addEventListener('webkitbeginfullscreen', () => syncNativeKorean(true));
    video.addEventListener('webkitendfullscreen', () => syncNativeKorean(false));
    if (nativeKorean) nativeKorean.mode = 'disabled';
    mute.addEventListener('click', toggleMute);
    fullscreen.addEventListener('click', toggleFullscreen);
    seek.addEventListener('pointerdown', () => { dragging = true; });
    seek.addEventListener('pointerup', () => { dragging = false; });
    seek.addEventListener('change', () => { dragging = false; });
    seek.addEventListener('input', () => {
      if (Number.isFinite(video.duration)) video.currentTime = Number(seek.value);
      updateTime();
    });
    koreanEnabled.addEventListener('change', () => { render(); syncNativeKorean(); });
    for (const element of trackElements) {
      element.addEventListener('load', () => { if (element === trackElement) trackReady(); });
      element.addEventListener('error', () => { if (element === trackElement) trackFailed(); });
      element.track.addEventListener('cuechange', () => { if (element === trackElement) render(); });
      element.track.mode = 'hidden';
    }
    translationSelect?.addEventListener('change', () => {
      const selected = trackElements.find(element => element.dataset.translationId === translationSelect.value);
      if (!selected) return;
      trackElement = selected;
      track = selected.track;
      loaded = false;
      koreanEnabled.disabled = true;
      status.textContent = '자막을 불러오는 중이에요.';
      render();
      if (selected.readyState === 2) trackReady();
      if (selected.readyState === 3) trackFailed();
    });
    for (const button of document.querySelectorAll('[data-seek-to]')) {
      button.addEventListener('click', () => {
        if (Number.isFinite(video.duration)) video.currentTime = clamp(Number(button.dataset.seekTo), 0, video.duration);
        updateTime();
      });
    }
    for (const event of ['timeupdate', 'seeked', 'play', 'pause', 'ended']) video.addEventListener(event, updateTime);
    video.addEventListener('loadedmetadata', () => { updateTime(); scheduleFit(); });
    video.addEventListener('volumechange', () => {
      mute.textContent = video.muted ? '🔇' : '🔊';
      mute.setAttribute('aria-pressed', String(video.muted));
      mute.setAttribute('aria-label', video.muted ? '소리 켜기' : '음소거');
      if (!video.muted && status.textContent.startsWith('소리가 꺼진')) status.textContent = '';
    });
    video.addEventListener('error', () => { status.textContent = '영상을 불러오지 못했어요.'; });
    document.addEventListener('keydown', event => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target;
      // Let form fields keep their own keys; buttons and links keep Space/Enter for activation.
      if (target !== seek && target.closest?.('input, select, textarea')) return;
      if (event.key === ' ' && target.closest?.('button, a')) return;
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      if ([' ', 'k', 'ArrowLeft', 'ArrowRight', 'm', 'f', 'n', 'p', 'c'].includes(key)) event.preventDefault(); else return;
      if (key === 'c' && !koreanEnabled.disabled) { koreanEnabled.checked = !koreanEnabled.checked; render(); }
      if (key === ' ' || key === 'k') togglePlay();
      if (key === 'm') toggleMute();
      if (key === 'f') toggleFullscreen();
      if (key === 'ArrowLeft') video.currentTime = Math.max(0, video.currentTime - 5);
      if (key === 'ArrowRight' && Number.isFinite(video.duration)) video.currentTime = Math.min(video.duration, video.currentTime + 5);
      if (key === 'n' && shell.dataset.nextUrl) goTo(shell.dataset.nextUrl);
      if (key === 'p' && shell.dataset.prevUrl) goTo(shell.dataset.prevUrl);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && shell.classList.contains('is-faux-fullscreen')) fauxFullscreen(false);
    });
    document.addEventListener('fullscreenchange', scheduleFit);
    document.addEventListener('webkitfullscreenchange', scheduleFit);
    window.addEventListener('resize', scheduleFit);
    window.visualViewport?.addEventListener('resize', scheduleFit);
    const observer = new ResizeObserver(scheduleFit);
    observer.observe(shell);
    window.addEventListener('pagehide', () => { disposed = true; observer.disconnect(); }, {once: true});
    if (trackElement.readyState === 2) trackReady();
    if (trackElement.readyState === 3) trackFailed();
    updateTime();
    scheduleFit();
    const params = new URLSearchParams(location.search);
    if (params.get('autoplay') === '1') {
      // Browsers may block autoplay with sound after a page change; fall back to muted playback.
      video.play().catch(() => {
        video.muted = true;
        video.play().then(() => { status.textContent = '소리가 꺼진 채 이어서 재생 중이에요. 🔊 버튼으로 켜 주세요.'; })
          .catch(() => { status.textContent = '재생 버튼을 눌러 이어서 보세요.'; });
      });
    }
    if (params.get('fullscreen') === '1') {
      // Browsers only allow real fullscreen after a click; keep the player filling the page instead.
      fauxFullscreen(true);
    }
  }

  for (const shell of document.querySelectorAll('[data-subtitle-player]')) enhance(shell);

  // Episode jump and playlist work even when a page has no Korean track to enhance.
  document.querySelector('[data-episode-jump]')?.addEventListener('change', event => {
    if (event.target.value) location.href = event.target.value;
  });
  const current = document.querySelector('.playlist [data-current]');
  const list = current && current.closest('ol');
  if (current && list) list.scrollTop = current.offsetTop - list.clientHeight / 2 + current.clientHeight / 2;
})();
