// Purchase helpers. Everything here stays in this browser (localStorage): the forwarder address
// and the order list are personal, so they are never sent to the server.
(() => {
  const PROFILE_KEY = 'gukawa.profile';
  const CART_KEY = 'gukawa.cart';
  const FX_KEY = 'gukawa.fx';
  const DUTY_FREE_USD = 150;

  const load = (key, fallback) => {
    try { const value = JSON.parse(localStorage.getItem(key)); return value ?? fallback; } catch { return fallback; }
  };
  const save = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } };
  const dataElement = document.getElementById('shop-data');
  const shopData = dataElement ? JSON.parse(dataElement.textContent) : { shops: {}, regions: {}, categories: [], fx: {} };
  const yen = value => '¥' + Math.round(value).toLocaleString('ko-KR');
  const won = value => Math.round(value).toLocaleString('ko-KR') + '원';
  const element = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key.includes('-')) node.setAttribute(key, value); else node[key] = value;
    }
    node.append(...children.filter(child => child !== null && child !== undefined));
    return node;
  };

  // --- toast & clipboard ---------------------------------------------------
  const toastNode = document.querySelector('[data-toast]');
  let toastTimer;
  function toast(message) {
    if (!toastNode) return;
    toastNode.textContent = message;
    toastNode.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastNode.hidden = true; }, 2200);
  }
  async function copy(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch { /* fall back below */ }
    // Plain http (no secure context): the old selection-based copy still works.
    const area = element('textarea', { value: text });
    area.setAttribute('readonly', '');
    area.style.position = 'fixed'; area.style.opacity = '0';
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    area.remove();
    return ok;
  }

  // --- forwarder address profile ---------------------------------------------
  const profile = () => load(PROFILE_KEY, {});
  function regionOf(prefecture) {
    for (const [region, names] of Object.entries(shopData.regions || {})) if (names.includes(prefecture)) return region;
    return '';
  }
  function renderProfileUses() {
    const data = profile();
    const filled = Object.values(data).some(Boolean);
    document.querySelectorAll('[data-profile-state]').forEach(node => { node.textContent = filled ? '저장됨' : '저장 안 됨'; });
    document.querySelectorAll('[data-field-value]').forEach(node => {
      const value = data[node.dataset.fieldValue] || '';
      node.textContent = value || '—';
      node.classList.toggle('is-empty', !value);
    });
    const region = regionOf(data.prefecture || '');
    document.querySelectorAll('[data-region-row]').forEach(row => row.classList.toggle('is-mine', row.dataset.regionRow === region));
    document.querySelectorAll('[data-region-note]').forEach(node => {
      node.textContent = region ? `내 배대지(${data.prefecture})는 ${region} 요금이에요.` : '배대지 주소의 都道府県을 저장하면 내 요금을 표시해요.';
    });
  }
  document.querySelectorAll('[data-profile-form]').forEach(form => {
    const fill = () => {
      const data = profile();
      for (const input of form.elements) if (input.name) input.value = data[input.name] || '';
    };
    fill();
    form.addEventListener('input', () => {
      const data = {};
      for (const input of form.elements) if (input.name) data[input.name] = input.value.trim();
      save(PROFILE_KEY, data);
      // Keep other profile forms on the page (if any) in sync.
      document.querySelectorAll('[data-profile-form]').forEach(other => { if (other !== form) for (const input of other.elements) if (input.name) input.value = data[input.name] || ''; });
      renderProfileUses();
      renderOrder();
    });
    form.querySelector('[data-profile-clear]')?.addEventListener('click', () => {
      if (!confirm('저장한 배대지 주소를 지울까요?')) return;
      save(PROFILE_KEY, {});
      document.querySelectorAll('[data-profile-form]').forEach(other => other.reset());
      renderProfileUses();
      renderOrder();
    });
  });
  document.querySelectorAll('[data-copy-field]').forEach(button => {
    button.addEventListener('click', async () => {
      const value = profile()[button.dataset.copyField] || '';
      if (!value) {
        toast('먼저 아래 "내 배대지 주소"를 채워 주세요.');
        const box = document.querySelector('[data-profile-box]');
        if (box) { box.open = true; box.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
        return;
      }
      toast(await copy(value) ? `복사했어요: ${value}` : '복사하지 못했어요. 길게 눌러 직접 복사해 주세요.');
    });
  });

  // --- cart ------------------------------------------------------------------------
  const cart = () => load(CART_KEY, []);
  const saveCart = items => { save(CART_KEY, items); renderOrder(); };
  function englishName(title) {
    for (const [keyword, name] of shopData.categories || []) if (title.includes(keyword)) return `Chiikawa ${name}`;
    return 'Chiikawa character goods';
  }
  function addToCart(item) {
    const items = cart();
    const existing = items.find(entry => entry.id === item.id);
    if (existing) {
      existing.qty += 1;
    } else {
      items.push({ qty: 1, en: englishName(item.title), ...item });
    }
    save(CART_KEY, items);
    renderOrder();
    return existing ? existing.qty : 1;
  }
  document.querySelectorAll('[data-add-cart]').forEach(button => {
    button.addEventListener('click', () => {
      const d = button.dataset;
      const qty = addToCart({ id: d.id, shop: d.shop, title: d.title, url: d.url,
                              price: d.price === '' ? 0 : Number(d.price), limit: d.limit ? Number(d.limit) : null });
      toast(qty > 1 ? `주문 정리에 ${qty}개 담겨 있어요.` : '주문 정리에 담았어요.');
    });
  });

  // --- exchange rates ------------------------------------------------------------------
  function rates() {
    const custom = load(FX_KEY, {});
    const auto = shopData.fx || {};
    return { usd_jpy: Number(custom.usd_jpy) || Number(auto.usd_jpy) || 0, usd_krw: Number(custom.usd_krw) || Number(auto.usd_krw) || 0,
             custom: Boolean(custom.usd_jpy || custom.usd_krw), updated: auto.updated || '' };
  }
  const fxForm = document.querySelector('[data-fx-form]');
  if (fxForm) {
    const custom = load(FX_KEY, {});
    const auto = shopData.fx || {};
    fxForm.usd_jpy.value = custom.usd_jpy || (auto.usd_jpy ? auto.usd_jpy.toFixed(2) : '');
    fxForm.usd_krw.value = custom.usd_krw || (auto.usd_krw ? auto.usd_krw.toFixed(2) : '');
    const note = fxForm.querySelector('[data-fx-note]');
    note.textContent = auto.updated ? `자동 환율 기준: ${auto.updated} (참고용, 관세청 고시 환율과 조금 달라요). 바꾸면 이 브라우저에 저장돼요.`
                                    : '자동 환율을 아직 받지 못했어요. 직접 넣어 주세요.';
    fxForm.addEventListener('input', () => {
      save(FX_KEY, { usd_jpy: fxForm.usd_jpy.value, usd_krw: fxForm.usd_krw.value });
      renderOrder();
    });
  }

  // --- order page ----------------------------------------------------------------------
  const page = document.querySelector('[data-order-page]');
  function shippingFor(shopKey, subtotal) {
    const shop = shopData.shops[shopKey];
    if (!shop || !shop.shipping) return { fee: null, note: '일본 내 배송비는 결제 화면에서 확인' };
    if (shop.free_over && subtotal >= shop.free_over) return { fee: 0, note: `${yen(shop.free_over)} 이상 무료 배송` };
    const region = regionOf(profile().prefecture || '');
    if (!region) return { fee: null, note: '배대지 都道府県을 저장하면 배송비를 계산해요' };
    const left = shop.free_over ? ` · ${yen(shop.free_over - subtotal)} 더 사면 무료` : '';
    return { fee: shop.shipping[region], note: `일본 내 배송비 ${region} ${yen(shop.shipping[region])}${left}` };
  }
  function formText(shopName, items) {
    const lines = [`[${shopName}]`];
    items.forEach((item, index) => {
      lines.push(`${index + 1}. ${item.en || englishName(item.title)} / ${item.qty}개 / 단가 ${item.price}엔 / ${item.url || '-'}`);
      lines.push(`   원래 이름: ${item.title}`);
    });
    lines.push(`합계: ${items.reduce((sum, item) => sum + item.price * item.qty, 0)}엔`);
    return lines.join('\n');
  }
  function renderOrder() {
    if (!page) return;
    const items = cart();
    const groupsNode = page.querySelector('[data-cart-groups]');
    const template = document.getElementById('group-template');
    groupsNode.replaceChildren();
    page.querySelector('[data-cart-empty]').hidden = items.length > 0;
    const groups = new Map();
    items.forEach((item, index) => {
      if (!groups.has(item.shop)) groups.set(item.shop, []);
      groups.get(item.shop).push({ ...item, index });
    });
    let totalJpy = 0;
    let unknownShipping = false;
    for (const [shopKey, entries] of groups) {
      const shopName = shopData.shops[shopKey]?.name || '기타 쇼핑몰';
      const group = template.content.firstElementChild.cloneNode(true);
      group.querySelector('[data-group-name]').textContent = shopName;
      const guide = group.querySelector('[data-group-guide]');
      if (shopData.shops[shopKey]) guide.href = `/shop/${shopKey}`; else guide.remove();
      const rows = group.querySelector('[data-rows]');
      const warnings = group.querySelector('[data-group-warnings]');
      for (const item of entries) {
        const update = changes => { const all = cart(); Object.assign(all[item.index], changes); saveCart(all); };
        const title = element('td', { className: 'order-title' },
          item.url ? element('a', { href: item.url, target: '_blank', rel: 'noreferrer', lang: 'ja', textContent: item.title })
                   : element('span', { lang: 'ja', textContent: item.title }),
          item.limit ? element('small', { className: 'limit', textContent: `1회 결제 ${item.limit}개까지` }) : null);
        const en = element('input', { value: item.en || '', 'aria-label': '영문명' });
        en.addEventListener('change', () => update({ en: en.value.trim() }));
        const price = element('input', { type: 'number', min: 0, step: 1, value: item.price, inputMode: 'numeric', 'aria-label': '단가(엔)' });
        price.addEventListener('change', () => update({ price: Math.max(0, Math.round(Number(price.value) || 0)) }));
        const qty = element('input', { type: 'number', min: 1, step: 1, value: item.qty, inputMode: 'numeric', 'aria-label': '수량' });
        qty.addEventListener('change', () => update({ qty: Math.max(1, Math.round(Number(qty.value) || 1)) }));
        const remove = element('button', { type: 'button', className: 'icon-button', textContent: '×', title: '빼기' });
        remove.setAttribute('aria-label', `${item.title} 빼기`);
        remove.addEventListener('click', () => { const all = cart(); all.splice(item.index, 1); saveCart(all); });
        rows.append(element('tr', {}, title, element('td', {}, en), element('td', {}, price), element('td', {}, qty),
                            element('td', { className: 'num', textContent: yen(item.price * item.qty) }), element('td', {}, remove)));
        if (item.limit && item.qty > item.limit) warnings.append(element('li', { textContent: `「${item.title}」은 1회 결제에 ${item.limit}개까지예요. 넘기면 주문 전체가 취소돼요.` }));
        if (!item.price) warnings.append(element('li', { textContent: `「${item.title}」의 단가를 넣어 주세요.` }));
      }
      const subtotal = entries.reduce((sum, item) => sum + item.price * item.qty, 0);
      const shipping = shippingFor(shopKey, subtotal);
      if (shipping.fee === null) unknownShipping = true;
      totalJpy += subtotal + (shipping.fee || 0);
      group.querySelector('[data-group-total]').textContent = `상품 ${yen(subtotal)} · ${shipping.note}` +
        (shipping.fee !== null ? ` · 합계 ${yen(subtotal + shipping.fee)}` : '');
      group.querySelector('[data-copy-form]').addEventListener('click', async () => {
        toast(await copy(formText(shopName, entries)) ? '신청서용 목록을 복사했어요.' : '복사하지 못했어요.');
      });
      if (!warnings.children.length) warnings.remove();
      groupsNode.append(group);
    }
    const totals = page.querySelector('[data-totals]');
    totals.replaceChildren();
    if (!items.length) { totals.append(element('p', { className: 'muted', textContent: '상품을 담으면 계산해요.' })); return; }
    const fx = rates();
    const usd = fx.usd_jpy ? totalJpy / fx.usd_jpy : null;
    const krw = usd !== null && fx.usd_krw ? usd * fx.usd_krw : null;
    totals.append(element('p', { className: 'total-line' }, element('span', { textContent: '물품가격 합계 (일본 내 배송비 포함)' }), element('b', { textContent: yen(totalJpy) })));
    if (usd !== null) totals.append(element('p', { className: 'total-line' }, element('span', { textContent: '달러 환산' }), element('b', { textContent: `$${usd.toFixed(2)}` })));
    if (krw !== null) totals.append(element('p', { className: 'total-line' }, element('span', { textContent: '원화 환산 (국제 배송비 제외)' }), element('b', { textContent: won(krw) })));
    if (usd !== null) {
      const over = usd > DUTY_FREE_USD;
      totals.append(element('p', { className: over ? 'verdict over' : 'verdict ok',
        textContent: over ? `면세 기준 $${DUTY_FREE_USD}를 넘어요. 관세·부가세가 붙을 수 있어요. 나눠 보낼 때는 한국 도착일이 겹치지 않게 출고하세요.`
                          : `면세 기준 $${DUTY_FREE_USD} 이하예요 ($${(DUTY_FREE_USD - usd).toFixed(2)} 여유). 같은 날 도착하는 다른 물건과 합쳐질 수 있어요.` }));
    } else {
      totals.append(element('p', { className: 'muted', textContent: '환율을 넣으면 면세 여부를 계산해요.' }));
    }
    if (unknownShipping) totals.append(element('p', { className: 'muted', textContent: '배송비를 모르는 쇼핑몰이 있어 실제 합계는 더 클 수 있어요.' }));
  }

  const addForm = document.querySelector('[data-add-form]');
  addForm?.addEventListener('submit', event => {
    event.preventDefault();
    const form = new FormData(addForm);
    const title = String(form.get('title')).trim();
    addToCart({ id: 'manual-' + Date.now(), shop: form.get('shop'), title, url: String(form.get('url')).trim(),
                price: Math.max(0, Math.round(Number(form.get('price')) || 0)), limit: Number(form.get('limit')) || null });
    const all = cart();
    all[all.length - 1].qty = Math.max(1, Math.round(Number(form.get('qty')) || 1));
    saveCart(all);
    addForm.reset();
    toast('추가했어요.');
  });
  page?.querySelector('[data-cart-clear]')?.addEventListener('click', () => {
    if (cart().length && confirm('담은 상품을 모두 비울까요?')) saveCart([]);
  });

  renderProfileUses();
  renderOrder();
})();
