async function send(path, body) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Gukawa-Action': 'admin' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(typeof data.detail === 'string' ? data.detail : '요청을 처리하지 못했어요.');
  }
}
document.querySelectorAll('[data-scan], [data-retry]').forEach(button => {
  button.addEventListener('click', async () => {
    const notice = document.querySelector('#notice');
    button.disabled = true;
    try {
      if (button.dataset.scan) {
        await send('/api/admin/scan', {mode: button.dataset.scan});
        notice.textContent = '확인을 요청했어요. 실행 중인 워커가 작업을 시작해요.';
      } else {
        await send(`/api/admin/episodes/${button.dataset.retry}/retry`, {});
        notice.textContent = '대기 목록에 다시 등록했어요.';
      }
    } catch (error) { notice.textContent = error.message; }
    finally { button.disabled = false; }
  });
});
