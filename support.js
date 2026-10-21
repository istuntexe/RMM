'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const sharing = location.pathname === '/share';
  let socket, peer, stream, session, pendingCandidates = [], queue = Promise.resolve();
  $('heading').textContent = sharing ? 'Share your screen' : 'View a shared screen';
  $('explanation').textContent = sharing
    ? 'Join using your enrolled device credential. Each session requires your approval and the browser’s screen picker. Keep this page open; closing it ends sharing.'
    : 'Connect using your admin token. The device user must approve each screen-sharing request. This is view-only support.';
  $('credential-label').firstChild.textContent = sharing ? 'Device token ' : 'Admin token ';
  $('state-label').hidden = !sharing;
  $('device').value = decodeURIComponent(location.hash.slice(1));
  const status = text => { $('status').textContent = text; };
  const send = message => {
    if (!socket || socket.readyState !== WebSocket.OPEN) throw Error('Signaling is disconnected');
    socket.send(JSON.stringify({...message, session_id: session}));
  };
  function clearPeer() {
    if (peer) { peer.onconnectionstatechange = null; peer.close(); }
    peer = null;
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    $('screen').srcObject = null;
    session = null;
    pendingCandidates = [];
    $('consent').hidden = true;
  }
  function disconnect() {
    if (session && socket?.readyState === WebSocket.OPEN) send({type:'end'});
    clearPeer();
    if (socket) socket.close();
    socket = null;
    $('connect').disabled = false;
    $('stop').hidden = true;
  }
  async function makePeer(iceServers) {
    peer = new RTCPeerConnection({iceServers});
    peer.onicecandidate = event => {
      if (event.candidate && socket?.readyState === WebSocket.OPEN && session) send({type:'candidate', candidate:event.candidate.toJSON()});
    };
    peer.onconnectionstatechange = () => {
      if (!peer) return;
      if (peer.connectionState === 'connected') status(sharing ? 'Sharing your screen — use Stop to end' : 'Connected — viewing shared screen');
      if (peer.connectionState === 'failed') { disconnect(); status('Media connection failed. Check TURN configuration and retry.'); }
    };
    if (sharing) {
      if (!stream) throw Error('Screen permission is required');
      stream.getTracks().forEach(track => peer.addTrack(track, stream));
    } else {
      peer.addTransceiver('video', {direction:'recvonly'});
      peer.ontrack = event => { $('screen').srcObject = event.streams[0] || new MediaStream([event.track]); };
    }
  }
  async function flushCandidates() {
    for (const candidate of pendingCandidates) await peer.addIceCandidate(candidate);
    pendingCandidates = [];
  }
  async function message(data) {
    if (data.type === 'error') { status('Support error: ' + data.error); return; }
    if (data.type === 'ready') { status('Ready — waiting for a support request'); return; }
    if (data.type === 'waiting') { session=data.session_id; status('Waiting for the device user’s approval'); return; }
    if (data.type === 'request' && sharing) {
      session = data.session_id;
      $('consent').hidden = false;
      status('Approval needed — request expires in ' + data.expires_in + ' seconds');
      return;
    }
    if (data.session_id !== session) return;
    if (data.type === 'ended') {
      clearPeer();
      status('Session ended: ' + data.reason);
      return;
    }
    if (data.type === 'accepted') {
      $('consent').hidden = true;
      await makePeer(data.ice_servers);
      status('Approved — connecting media');
      if (!sharing) {
        await peer.setLocalDescription(await peer.createOffer());
        send({type:'offer', sdp:peer.localDescription.sdp});
      }
    } else if (data.type === 'offer' && sharing) {
      await peer.setRemoteDescription({type:'offer', sdp:data.sdp});
      await flushCandidates();
      await peer.setLocalDescription(await peer.createAnswer());
      send({type:'answer', sdp:peer.localDescription.sdp});
    } else if (data.type === 'answer' && !sharing) {
      await peer.setRemoteDescription({type:'answer', sdp:data.sdp});
      await flushCandidates();
    } else if (data.type === 'candidate' && peer) {
      if (peer.remoteDescription) await peer.addIceCandidate(data.candidate);
      else pendingCandidates.push(data.candidate);
    }
  }
  $('join').addEventListener('submit', async event => {
    event.preventDefault();
    if (socket) return;
    $('connect').disabled = true;
    try {
      const response = await fetch('/api/support/config');
      if (!response.ok) throw Error('Could not load support configuration');
      const config = await response.json();
      const url = config.websocket_url || (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
      if (location.protocol === 'https:' && !url.startsWith('wss://')) throw Error('Secure signaling is required');
      const connection = new WebSocket(url);
      socket = connection;
      socket.onopen = () => {
        if (socket !== connection) return;
        socket.send(JSON.stringify({type:'auth', role:sharing?'agent':'viewer', device_id:$('device').value, token:$('credential').value}));
        $('credential').value = '';
        $('stop').hidden = false;
      };
      socket.onmessage = event => { if (socket !== connection) return; queue = queue.then(() => message(JSON.parse(event.data))).catch(error => { disconnect(); status('Support failed: ' + error.message); }); };
      socket.onerror = () => status('Could not connect to signaling');
      socket.onclose = () => {
        if (socket !== connection) return;
        clearPeer(); socket = null; $('connect').disabled = false; $('stop').hidden = true;
        if (!['Session ended:', 'Support error:'].some(prefix => $('status').textContent.startsWith(prefix))) status('Disconnected — reconnect to request a new session');
      };
      status('Connecting to signaling');
    } catch (error) { disconnect(); status(error.message); }
  });
  $('accept').addEventListener('click', async () => {
    $('accept').disabled = true;
    const requestedSession = session;
    try {
      if (!navigator.mediaDevices?.getDisplayMedia) throw Error('Screen sharing requires HTTPS or localhost and a supported browser');
      const selected = await navigator.mediaDevices.getDisplayMedia({video:true, audio:false});
      if (!session || session !== requestedSession) { selected.getTracks().forEach(track => track.stop()); return; }
      stream = selected;
      $('screen').srcObject = stream;
      stream.getVideoTracks()[0].addEventListener('ended', () => { disconnect(); status('Screen sharing stopped'); }, {once:true});
      send({type:'accept'});
    } catch (error) {
      if (session && socket?.readyState === WebSocket.OPEN) send({type:'reject'});
      clearPeer(); status('Screen permission was not granted: ' + error.message);
    } finally { $('accept').disabled = false; }
  });
  $('reject').addEventListener('click', () => { send({type:'reject'}); clearPeer(); status('Request rejected'); });
  $('stop').addEventListener('click', () => { disconnect(); status('Stopped by user'); });
  $('state-file').addEventListener('change', async event => {
    try {
      const file = event.target.files[0];
      if (!file || file.size > 16384) throw Error('Invalid state file');
      const data = JSON.parse(await file.text());
      if (typeof data.id !== 'string' || typeof data.device_token !== 'string') throw Error('Not an enrolled device state file');
      if (data.server && new URL(data.server).origin !== location.origin) throw Error('Device state belongs to another server');
      $('device').value = data.id; $('credential').value = data.device_token;
      event.target.value = '';
      status('Device credential loaded locally. Connect to receive requests.');
    } catch (error) { status(error.message); }
  });
  window.addEventListener('beforeunload', disconnect);
})();
