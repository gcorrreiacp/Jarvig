// aura.js — Aura, a voice-reactive orb you drive with text.
//
//   import { createAura } from './aura.js';
//   const aura = createAura(document.getElementById('aura'), { elevenLabsKey: '...' });
//   await aura.say('Hello, I am Aura.');
//
// Voice sources, picked automatically in this order:
//   ttsUrl         → your own server endpoint: POST {text} → audio (keeps API keys off the browser)
//   elevenLabsKey  → calls ElevenLabs straight from the browser (fine for prototypes)
//   (neither)      → the browser's built-in speech voice
//
// Requires three.js (`npm install three`, or an import map pointing "three" at a CDN).

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

const DEFAULTS = {
  ttsUrl: null,
  elevenLabsKey: null,
  elevenLabsVoiceId: 'Xb7hH8MSUJpSbSDYk0k2',   // "Alice" – clear British female ("Lily": pFZP5JQG7iQjIQuC4Bku)
  elevenLabsModel: 'eleven_multilingual_v2',
  browserVoice: /Serena|Kate|Martha|Stephanie|Google UK English Female|Samantha|Karen|Moira|Tessa|Female/i, // female built-in voices, best first
  pitch: 1.0,
  rate: 0.95,
  fx: true,                                      // subtle metallic "AI" processing (not for browser voice)
  background: '#02060c',
  colorA: '#0a4cff',
  colorB: '#3ff0ff',
  intensity: 1,                                  // how much the ball deforms
};

// ---------- Shaders ----------
const NOISE = /* glsl */`
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const VERTEX = /* glsl */`
uniform float uTime, uLevel, uLow, uHigh, uIntensity;
varying float vDisp;
varying vec3 vNormal, vView;
${NOISE}
float displace(vec3 p){
  float breathe = snoise(p*1.1 + uTime*0.2) * 0.05;
  float body    = snoise(p*1.6 + vec3(0.0, uTime*0.6, 0.0)) * (0.03 + uLow*0.35*uIntensity);
  float detail  = snoise(p*3.2 + uTime*1.2) * uHigh*0.08*uIntensity;
  return breathe + body + detail + uLevel*0.1*uIntensity;
}
void main(){
  vec3 p = position;
  vec3 newPos = p + normal * displace(normalize(p));
  vec3 helper = abs(normal.y) > 0.9 ? vec3(1.0,0.0,0.0) : vec3(0.0,1.0,0.0);
  vec3 t = normalize(cross(normal, helper));
  vec3 b = normalize(cross(normal, t));
  float e = 0.02;
  vec3 pt = p + t*e; vec3 pb = p + b*e;
  pt += normalize(pt) * displace(normalize(pt));
  pb += normalize(pb) * displace(normalize(pb));
  vNormal = normalize(normalMatrix * normalize(cross(pt - newPos, pb - newPos)));
  vec4 mv = modelViewMatrix * vec4(newPos, 1.0);
  vView = normalize(-mv.xyz);
  vDisp = displace(normalize(p));
  gl_Position = projectionMatrix * mv;
}`;

const FRAGMENT = /* glsl */`
uniform float uLevel;
uniform vec3 uColorA, uColorB;
varying float vDisp;
varying vec3 vNormal, vView;
void main(){
  float fres = pow(1.0 - clamp(dot(normalize(vNormal), normalize(vView)), 0.0, 1.0), 1.6);
  vec3 col = mix(uColorA, uColorB, clamp(0.35 + vDisp*1.0, 0.0, 1.0));
  col = col*0.32 + col*fres*(1.0 + uLevel*0.5);
  gl_FragColor = vec4(col, 1.0);
}`;

const WIRE_FRAGMENT = /* glsl */`
uniform float uLevel; uniform vec3 uColorB; varying vec3 vNormal, vView;
void main(){
  float f = pow(clamp(1.0 - dot(normalize(vNormal), normalize(vView)), 0.0, 1.0), 1.5);
  gl_FragColor = vec4(uColorB*(0.08 + f*0.35 + uLevel*0.25), 0.25);
}`;

// First voice matching the earliest name in the pattern (so "Serena" beats "Samantha"), English preferred
function pickVoice(voices, pattern) {
  const names = pattern.source.split('|');
  const english = voices.filter(v => /^en/i.test(v.lang));
  for (const n of names) {
    const re = new RegExp(n, pattern.flags);
    const v = english.find(v => re.test(v.name)) || voices.find(v => re.test(v.name));
    if (v) return v;
  }
  return null;
}

export function createAura(container, options = {}) {
  const opt = { ...DEFAULTS, ...options };

  // ---------- Scene ----------
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor(opt.background);
  renderer.domElement.style.display = 'block';
  // The bloom glow tints the whole canvas; fade its edges so it blends into any page
  renderer.domElement.style.maskImage = renderer.domElement.style.webkitMaskImage =
    'radial-gradient(circle closest-side, #000 70%, transparent 100%)';
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 0, 6);

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.8, 0.5, 0.3);
  composer.addPass(bloom);

  const uniforms = {
    uTime: { value: 0 }, uLevel: { value: 0 }, uLow: { value: 0 }, uHigh: { value: 0 },
    uIntensity: { value: opt.intensity },
    uColorA: { value: new THREE.Color(opt.colorA) }, uColorB: { value: new THREE.Color(opt.colorB) },
  };
  const targetA = new THREE.Color(opt.colorA), targetB = new THREE.Color(opt.colorB);
  const orb = new THREE.Mesh(new THREE.IcosahedronGeometry(1.4, 64),
    new THREE.ShaderMaterial({ vertexShader: VERTEX, fragmentShader: FRAGMENT, uniforms }));
  const wire = new THREE.Mesh(new THREE.IcosahedronGeometry(1.5, 14), new THREE.ShaderMaterial({
    vertexShader: VERTEX, fragmentShader: WIRE_FRAGMENT, uniforms, wireframe: true, transparent: true,
    depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending }));
  scene.add(orb, wire);

  function resize() {
    const w = container.clientWidth || 300, h = container.clientHeight || 300;
    camera.aspect = w / h; camera.updateProjectionMatrix();
    renderer.setSize(w, h); composer.setSize(w, h);
  }
  const ro = new ResizeObserver(resize); ro.observe(container); resize();

  // ---------- Audio ----------
  let ctx, analyser, freq, srcNode, mediaEl, stream;
  let browserSpeaking = false, simulated = 0;
  let finishCurrent = null;   // resolves the pending say() promise
  let gen = 0;                 // bumped by stop(), so slow fetches/mic prompts that finish late are dropped
  let micGen = 0;              // bumped by releaseMic(), for a mic prompt still open when listening ends

  function ensureCtx() {
    if (ctx) return;
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.85;
    freq = new Uint8Array(analyser.frequencyBinCount);
  }

  function stop() {
    gen++;
    speechSynthesis.cancel();
    browserSpeaking = false;
    if (srcNode) { srcNode.disconnect(); srcNode = null; }
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    if (mediaEl) { mediaEl.pause(); URL.revokeObjectURL(mediaEl.src); mediaEl = null; }
    finishCurrent?.(); finishCurrent = null;
  }

  // Subtle metallic doubling + small room, for a processed assistant sound
  function withFx(input) {
    const out = ctx.createGain();
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -24; comp.ratio.value = 3;
    input.connect(comp); comp.connect(out);
    const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 900;
    const delay = ctx.createDelay(); delay.delayTime.value = 0.012;
    const dGain = ctx.createGain(); dGain.gain.value = 0.35;
    comp.connect(hp); hp.connect(delay); delay.connect(dGain); dGain.connect(out);
    const verb = ctx.createConvolver();
    const len = ctx.sampleRate * 0.6, ir = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let c = 0; c < 2; c++) {
      const d = ir.getChannelData(c);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3);
    }
    verb.buffer = ir;
    const vGain = ctx.createGain(); vGain.gain.value = 0.12;
    comp.connect(verb); verb.connect(vGain); vGain.connect(out);
    return out;
  }

  function playBlob(blob) {
    return new Promise((resolve, reject) => {
      mediaEl = new Audio(URL.createObjectURL(blob));
      srcNode = ctx.createMediaElementSource(mediaEl);
      const out = opt.fx ? withFx(srcNode) : srcNode;
      out.connect(analyser); out.connect(ctx.destination);
      finishCurrent = resolve;
      mediaEl.onended = () => { finishCurrent = null; resolve(); };
      mediaEl.play().catch(reject);
    });
  }

  async function fetchSpeech(text) {
    let res;
    if (opt.ttsUrl) {
      res = await fetch(opt.ttsUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }) });
    } else {
      res = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${opt.elevenLabsVoiceId}?output_format=mp3_44100_128`, {
        method: 'POST',
        headers: { 'xi-api-key': opt.elevenLabsKey, 'Content-Type': 'application/json', 'Accept': 'audio/mpeg' },
        body: JSON.stringify({ text, model_id: opt.elevenLabsModel,
          voice_settings: { stability: 0.6, similarity_boost: 0.8, style: 0.2 } }),
      });
    }
    if (!res.ok) throw new Error(`TTS failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
    return res.blob();
  }

  // The browser voice can't be routed into Web Audio, so its movement is simulated from word timing.
  function sayWithBrowser(text) {
    return new Promise((resolve, reject) => {
      const u = new SpeechSynthesisUtterance(text);
      const v = pickVoice(speechSynthesis.getVoices(), opt.browserVoice);
      if (v) u.voice = v;
      u.pitch = opt.pitch; u.rate = opt.rate;
      u.onstart = () => { browserSpeaking = true; };
      u.onboundary = () => { simulated = 0.75; };
      u.onend = () => { browserSpeaking = false; finishCurrent = null; resolve(); };
      u.onerror = (e) => {
        browserSpeaking = false; finishCurrent = null;
        // "not-allowed": no click or key press yet (autoplay rules); the caller waits for one.
        if (e.error === 'not-allowed') reject(new DOMException('Speech needs a click first', 'NotAllowedError'));
        else resolve();
      };
      finishCurrent = resolve;
      speechSynthesis.resume();   // clears Chrome's occasional stuck "paused" state
      speechSynthesis.speak(u);
    });
  }

  /** Speak the text and animate the orb. Resolves when speech finishes (or is stopped). */
  async function say(text) {
    stop();
    const mine = gen;
    if (!opt.ttsUrl && !opt.elevenLabsKey) return sayWithBrowser(text);
    ensureCtx();
    // Without a click yet, the audio can't start: say so instead of waiting forever.
    await Promise.race([ctx.resume(), new Promise(r => setTimeout(r, 400))]);
    if (ctx.state !== 'running') throw new DOMException('Audio needs a click first', 'NotAllowedError');
    const blob = await fetchSpeech(text);
    if (mine !== gen) return;   // stopped or replaced while the audio was downloading
    return playBlob(blob);
  }

  /** React to the microphone instead (e.g. while the user is talking). */
  async function useMic() {
    stop(); const mine = gen, mineMic = micGen;
    ensureCtx(); await ctx.resume();
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (mine !== gen || mineMic !== micGen) { s.getTracks().forEach(t => t.stop()); return; }
    stream = s;
    srcNode = ctx.createMediaStreamSource(stream);
    srcNode.connect(analyser);   // not to the speakers, to avoid feedback
  }

  /** Stop reacting to the microphone without interrupting speech that may already be playing. */
  function releaseMic() {
    micGen++;
    if (!stream) return;
    stream.getTracks().forEach(t => t.stop()); stream = null;
    srcNode?.disconnect(); srcNode = null;
  }

  function readAudio(t) {
    if (analyser && srcNode) {
      analyser.getByteFrequencyData(freq);
      const n = freq.length, lowEnd = Math.floor(n * 0.08), highStart = Math.floor(n * 0.25);
      let low = 0, high = 0, all = 0;
      for (let i = 0; i < n; i++) {
        const v = freq[i] / 255; all += v;
        if (i < lowEnd) low += v; else if (i > highStart) high += v;
      }
      return { level: all / n * 2.2, low: low / lowEnd * 1.1, high: high / (n - highStart) * 3.5 };
    }
    if (browserSpeaking) {
      const syll = 0.5 + 0.5 * Math.sin(t * 9.0) * Math.sin(t * 3.7);
      simulated = Math.max(simulated - 0.012, 0.3);
      const env = simulated * (0.6 + 0.4 * syll);
      return { level: env * 0.7, low: env, high: env * 0.5 };
    }
    return { level: 0, low: 0, high: 0 };
  }

  // ---------- Loop ----------
  const clock = new THREE.Clock();
  const s = { level: 0, low: 0, high: 0 };
  let raf;
  function tick() {
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    const a = readAudio(t);
    for (const k in s) {
      const target = Math.min(a[k], 1.2);
      s[k] += (target - s[k]) * (1 - Math.exp(-dt * (target > s[k] ? 14 : 4)));
    }
    uniforms.uTime.value = t;
    uniforms.uLevel.value = s.level;
    uniforms.uLow.value = s.low;
    uniforms.uHigh.value = s.high;
    const fade = 1 - Math.exp(-dt * 3);   // ease colour changes instead of snapping
    uniforms.uColorA.value.lerp(targetA, fade);
    uniforms.uColorB.value.lerp(targetB, fade);
    bloom.strength = 0.8 + Math.min(s.level, 1) * 0.4;
    orb.rotation.y = wire.rotation.y = t * 0.15;
    orb.rotation.x = wire.rotation.x = Math.sin(t * 0.2) * 0.2;
    orb.scale.setScalar(1 + Math.min(s.level, 1) * 0.05);
    wire.scale.copy(orb.scale);
    composer.render();
    raf = requestAnimationFrame(tick);
  }
  tick();

  function destroy() {
    stop(); cancelAnimationFrame(raf); ro.disconnect();
    ctx?.close();
    orb.geometry.dispose(); orb.material.dispose(); wire.geometry.dispose(); wire.material.dispose();
    composer.dispose?.(); renderer.dispose();
    renderer.forceContextLoss();   // free the WebGL context right away (React remounts often in dev)
    renderer.domElement.remove();
  }

  return {
    say, stop, useMic, releaseMic, destroy,
    /** Change options later, e.g. aura.set({ elevenLabsVoiceId: '...', fx: false }) */
    set(changes) {
      Object.assign(opt, changes);
      if ('intensity' in changes) uniforms.uIntensity.value = opt.intensity;
      if ('colorA' in changes) targetA.set(opt.colorA);
      if ('colorB' in changes) targetB.set(opt.colorB);
      if ('background' in changes) renderer.setClearColor(opt.background);
    },
    get level() { return s.level; },
  };
}
