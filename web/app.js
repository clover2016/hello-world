const state = {
  participant: JSON.parse(localStorage.getItem('lanim.participant') || 'null'),
  latestMessageId: 0,
  participants: [],
};

const $ = (id) => document.getElementById(id);
const messagesEl = $('messages');
const participantsEl = $('participants');
const messageForm = $('message-form');
const identityForm = $('identity-form');
const nameInput = $('name-input');

if (state.participant) nameInput.value = state.participant.name;

function formatTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString();
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function ensureParticipant() {
  if (state.participant?.id) return state.participant;
  const preferredName = nameInput.value.trim() || `Human-${Math.floor(Math.random() * 1000)}`;
  const data = await request('/api/participants', {
    method: 'POST',
    body: JSON.stringify({ name: preferredName, role: 'human' }),
  });
  state.participant = data.participant;
  localStorage.setItem('lanim.participant', JSON.stringify(state.participant));
  nameInput.value = state.participant.name;
  return state.participant;
}

function renderMessages(messages) {
  for (const msg of messages) {
    state.latestMessageId = Math.max(state.latestMessageId, msg.id);
    const item = document.createElement('article');
    item.className = `message ${msg.sender_role}`;
    item.innerHTML = `
      <div class="meta"><strong></strong><span></span><span></span></div>
      <div class="text"></div>
    `;
    item.querySelector('strong').textContent = msg.sender_name;
    item.querySelector('.meta span:nth-child(2)').textContent = msg.sender_role;
    item.querySelector('.meta span:nth-child(3)').textContent = formatTime(msg.created_at);
    item.querySelector('.text').textContent = msg.text;
    messagesEl.appendChild(item);
  }
  if (messages.length) messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderParticipants(participants) {
  state.participants = participants;
  participantsEl.innerHTML = '';
  for (const person of participants) {
    const item = document.createElement('div');
    item.className = 'participant';
    item.innerHTML = `
      <div class="row"><strong></strong><span class="badge"></span></div>
      <div class="row"><span class="status"></span><span class="badge last-seen"></span></div>
      <label>显示名 <input class="rename" maxlength="80"></label>
      <label>备注/指令 <textarea class="settings" rows="2" placeholder="人类可在这里记录 agent 设置"></textarea></label>
      <button type="button">保存设置</button>
    `;
    item.querySelector('strong').textContent = person.name;
    item.querySelector('.badge').textContent = person.role;
    item.querySelector('.status').textContent = person.online ? 'online' : 'offline';
    item.querySelector('.status').className = `status ${person.online ? 'online' : 'offline'}`;
    item.querySelector('.last-seen').textContent = `last seen ${formatTime(person.last_seen_at)}`;
    item.querySelector('.rename').value = person.name;
    item.querySelector('.settings').value = person.settings?.note || '';
    item.querySelector('button').addEventListener('click', async () => {
      const name = item.querySelector('.rename').value.trim();
      const note = item.querySelector('.settings').value.trim();
      await request(`/api/participants/${person.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name, settings: { note } }),
      });
      if (state.participant?.id === person.id) {
        state.participant.name = name;
        localStorage.setItem('lanim.participant', JSON.stringify(state.participant));
      }
      await refresh(false);
    });
    participantsEl.appendChild(item);
  }
}

async function refresh(wait = true) {
  const data = await request(`/api/state?after=${state.latestMessageId}&wait=${wait ? 1 : 0}`);
  renderParticipants(data.participants);
  renderMessages(data.messages);
}

async function poll() {
  while (true) {
    try {
      await refresh(true);
    } catch (error) {
      console.error(error);
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }
}

identityForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.participant?.id) {
    const data = await request(`/api/participants/${state.participant.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name: nameInput.value.trim() || state.participant.name }),
    });
    state.participant = data.participant;
  } else {
    await ensureParticipant();
  }
  localStorage.setItem('lanim.participant', JSON.stringify(state.participant));
  await refresh(false);
});

messageForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const participant = await ensureParticipant();
  const text = $('message-input').value.trim();
  if (!text) return;
  await request('/api/messages', {
    method: 'POST',
    body: JSON.stringify({ participant_id: participant.id, text }),
  });
  $('message-input').value = '';
  await refresh(false);
});

setInterval(() => {
  if (state.participant?.id) request(`/api/participants/${state.participant.id}/heartbeat`, { method: 'POST', body: '{}' }).catch(console.error);
}, 15000);

ensureParticipant().then(() => refresh(false)).then(poll).catch(console.error);
