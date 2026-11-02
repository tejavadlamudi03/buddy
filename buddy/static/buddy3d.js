// Buddy in 3D. The page keeps working out his pose in `toon`, and this draws it, so every mood and gag carries over.
// The flat drawing stays underneath: without WebGL, or if this file can't load, nothing changes.
import * as THREE from './three.module.min.js';

const SKIN = '#f6cfa4', NOSE = '#f0bd8d', HAIR = '#1f1510', TOP = '#d3222a', COLLAR = '#9e141b', TROUSERS = '#24242b', SHOE = '#17171b';
const WHITE = '#fffdf6', INK = '#2a1e14', MOUTH = '#5c141c', TONGUE = '#e8606b', CHEEK = '#f0836f';
const HEAD_Y = .75, HEAD = {x:.7, y:.83, z:.66};  // the head's centre and size, from the point it tilts around
const HEAD_SCALE = .62, NECK_Y = 1.3;  // a small head on a tall body, so he has a gym build and not a toy's
const VIEW_HEIGHT = 4.5;  // the canvas is 1.5 times Buddy's button, so raised hands and falls aren't cut off
const FIELD_OF_VIEW = 18;

// The page poses him in the flat drawing's 300 by 300 space.
const bodyX = x => (x - 150) / 100, bodyY = y => (280 - y) / 100;
const headX = x => (x - 150) / 100, headY = y => (182 - y) / 100;
const headSurface = (x, y) => {
  const across = x / HEAD.x, up = (y - HEAD_Y) / HEAD.y;
  return HEAD.z * Math.sqrt(Math.max(.02, 1 - across * across - up * up));
};
const mix = (a, b, amount) => a + (b - a) * amount;
const degrees = THREE.MathUtils.degToRad;

const material = (color, roughness = .6, extra = {}) => new THREE.MeshStandardMaterial({color, roughness, ...extra});
function ball(color, radius, x = 0, y = 0, z = 0, scale = [1, 1, 1], roughness = .6) {
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 32, 24), typeof color === 'string' ? material(color, roughness) : color);
  mesh.position.set(x, y, z);
  mesh.scale.set(...scale);
  mesh.castShadow = true;
  return mesh;
}
function flat(points, color, z, depth = .025) {
  const shape = new THREE.Shape(points.map(([x, y]) => new THREE.Vector2(x, y)));
  const mesh = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, {depth, bevelEnabled:true, bevelSize:.008, bevelThickness:.008, bevelSegments:2, curveSegments:8}), material(color, .7));
  mesh.position.z = z;
  mesh.castShadow = true;
  return mesh;
}
function arc(points, radius, color) {
  const curve = new THREE.CatmullRomCurve3(points.map(([x, y, z]) => new THREE.Vector3(x, y, z)));
  const mesh = new THREE.Mesh(new THREE.TubeGeometry(curve, 20, radius, 10), material(color, .7));
  const cap = geometry => { const end = new THREE.Mesh(new THREE.SphereGeometry(radius, 10, 8), mesh.material); end.position.copy(geometry); mesh.add(end); };
  cap(curve.getPoint(0)); cap(curve.getPoint(1));
  return mesh;
}
function sprite(text, color, size) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 128;
  const paint = canvas.getContext('2d');
  paint.font = '700 96px system-ui'; paint.textAlign = 'center'; paint.textBaseline = 'middle';
  paint.lineWidth = 8; paint.strokeStyle = INK; paint.fillStyle = color;
  paint.strokeText(text, 64, 68); paint.fillText(text, 64, 68);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const mesh = new THREE.Sprite(new THREE.SpriteMaterial({map:texture, transparent:true, depthWrite:false, toneMapped:false}));
  mesh.scale.set(size, size, 1);
  return mesh;
}

// A smooth surface between two curves on the face, rewritten every frame as the mouth moves.
class Patch {
  constructor(color, basic = false) {
    this.across = 26; this.rows = 5;
    const index = [];
    for (let a = 0; a < this.across - 1; a++) for (let r = 0; r < this.rows - 1; r++) {
      const here = a * this.rows + r;
      index.push(here, here + this.rows, here + 1, here + 1, here + this.rows, here + this.rows + 1);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(this.across * this.rows * 3), 3));
    geometry.setIndex(index);
    this.mesh = new THREE.Mesh(geometry, basic ? new THREE.MeshBasicMaterial({color, side:THREE.DoubleSide}) : material(color, .45, {side:THREE.DoubleSide}));
    this.basic = basic;
  }
  draw(xAt, topAt, bottomAt, lift) {
    const position = this.mesh.geometry.attributes.position;
    for (let a = 0; a < this.across; a++) for (let r = 0; r < this.rows; r++) {
      const along = a / (this.across - 1), x = xAt(along), y = mix(topAt(along), bottomAt(along), r / (this.rows - 1));
      position.setXYZ(a * this.rows + r, x, y, headSurface(x, y) + lift);
    }
    position.needsUpdate = true;
    if (!this.basic) this.mesh.geometry.computeVertexNormals();
    this.mesh.geometry.computeBoundingSphere();
  }
}

function buildEye(cx, cy, radius) {
  const x = headX(cx), y = headY(cy), z = headSurface(x, y) - radius * .3;
  const eye = new THREE.Group();
  eye.position.set(x, y, z);
  const inside = new THREE.Group();
  inside.scale.z = .62;
  eye.add(inside);
  inside.add(ball(WHITE, radius, 0, 0, 0, [1, 1, 1], .18));
  const pupil = new THREE.Group();
  pupil.add(ball(INK, radius * .4, 0, 0, 0, [1, 1, .35], .12), ball('#ffffff', radius * .12, radius * .14, radius * .15, radius * .13, [1, 1, 1], .1));
  inside.add(pupil);
  const lid = new THREE.Mesh(new THREE.SphereGeometry(radius * 1.05, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2), material(SKIN));
  inside.add(lid);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(radius * 1.02, .011, 10, 48), material(INK, .5));
  rim.position.z = radius * .02;
  eye.add(rim);
  const curve = (dip, lift) => arc([[-radius * .9, lift, radius * .55], [0, lift + dip, radius * .7], [radius * .9, lift, radius * .55]], .024, INK);
  const shut = curve(-radius * .28, 0), smile = curve(radius * .55, -radius * .1);
  eye.add(shut, smile);
  return {group:eye, inside, pupil, lid, shut, smile, radius};
}

function buildBuddy() {
  const whole = new THREE.Group();
  const head = new THREE.Group();
  head.position.y = NECK_Y;
  whole.add(head);

  const skull = ball(SKIN, 1, 0, HEAD_Y, 0, [HEAD.x, HEAD.y, HEAD.z], .62);
  head.add(skull);
  for (const side of [-1, 1]) {
    head.add(ball(SKIN, 1, side * .71, HEAD_Y + .02, -.04, [.1, .18, .08]));
    head.add(ball(NOSE, 1, side * .73, HEAD_Y + .02, -.015, [.05, .1, .05]));
  }
  // Curls all over, on a dark base so no scalp shows between them. Round the back they come down to the neck, but at the front they stop above the eyes.
  const hair = material(HAIR, .36), curl = new THREE.SphereGeometry(1, 18, 14);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 24, 0, Math.PI * 2, 0, 1.0), hair);
  cap.position.y = HEAD_Y + .01;
  cap.scale.set(HEAD.x * 1.02, HEAD.y * 1.02, HEAD.z * 1.03);
  const back = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 24, 0, Math.PI * 2, 0, 1.42), hair);
  back.position.y = HEAD_Y;
  back.rotation.x = -.95;
  back.scale.set(HEAD.x * 1.015, HEAD.y * 1.015, HEAD.z * 1.025);
  head.add(cap, back);
  for (const [tilt, count, size] of [[.16, 5, .17], [.48, 11, .175], [.8, 15, .165], [1.08, 17, .15], [1.36, 15, .135]]) {
    for (let i = 0; i < count; i++) {
      const round = i / count * Math.PI * 2 + tilt * 2, x = Math.sin(tilt) * Math.cos(round), z = Math.sin(tilt) * Math.sin(round);
      if ((tilt > 1 && z > .12) || (tilt > 1.3 && z > -.2)) continue;
      const lock = new THREE.Mesh(curl, hair), grown = size * (1 + Math.sin(i * 2.3 + tilt * 9) * .16);
      lock.position.set(HEAD.x * x, HEAD_Y + HEAD.y * Math.cos(tilt), HEAD.z * z);
      lock.scale.set(grown * 1.12, grown * .92, grown);
      lock.rotation.set(Math.sin(i * 1.7) * .5, round, Math.cos(i * 3.1) * .5);
      lock.castShadow = true;
      head.add(lock);
    }
  }
  const shades = new THREE.Group(), glass = material('#08080d', .08, {metalness:.75}), frame = material('#141418', .3);
  for (const side of [-1, 1]) {
    const lens = ball(glass, 1, side * .285, .9, .7, [.285, .205, .035]);
    const rim = new THREE.Mesh(new THREE.TorusGeometry(1, .08, 8, 36), frame);
    rim.position.copy(lens.position);
    rim.scale.set(.285, .205, .28);
    shades.add(lens, rim, arc([[side * .55, .94, .62], [side * .69, .93, .34], [side * .71, .88, .02]], .018, '#141418'));
  }
  shades.add(arc([[-.05, .96, .7], [0, .99, .72], [.05, .96, .7]], .02, '#141418'));
  shades.visible = false;
  head.add(shades);

  const eyeL = buildEye(122, 93, .21), eyeR = buildEye(179, 92, .24);
  head.add(eyeL.group, eyeR.group);

  const browL = arc([[-.2, -.03, 0], [0, .05, .02], [.2, -.02, 0]], .045, INK), browR = arc([[-.22, -.02, 0], [0, .05, .02], [.22, -.03, 0]], .045, INK);
  head.add(browL, browR);

  const nose = new THREE.Group();
  nose.add(ball(NOSE, .1, 0, .15, .02, [.75, 1.5, .9]), ball(NOSE, .15, 0, 0, .06, [1.08, .92, 1.02], .5));
  nose.position.set(0, headY(124), headSurface(0, headY(124)));
  head.add(nose);

  const cheekFor = () => new THREE.MeshStandardMaterial({color:CHEEK, roughness:.8, transparent:true, opacity:0, depthWrite:false});
  const cheekL = ball(cheekFor(), 1, headX(98), headY(128), headSurface(headX(98), headY(128)) - .012, [.14, .09, .03]);
  const cheekR = ball(cheekFor(), 1, headX(202), headY(128), headSurface(headX(202), headY(128)) - .012, [.14, .09, .03]);
  cheekL.castShadow = cheekR.castShadow = false;
  head.add(cheekL, cheekR);

  const lips = new Patch(INK, true), mouth = new Patch(MOUTH, true), teeth = new Patch(WHITE), tongue = new Patch(TONGUE);
  head.add(lips.mesh, mouth.mesh, teeth.mesh, tongue.mesh);
  const lick = ball(TONGUE, 1, 0, 0, 0, [.12, .17, .04], .4);
  head.add(lick);
  const sweat = ball('#7cc8ee', .05, headX(214), headY(66), .3, [.8, 1.25, .8], .1);
  head.add(sweat);

  // A fitted T-shirt over a wide chest and a narrow waist.
  const lathe = (outer, color, depth = .62) => {
    const edge = new THREE.SplineCurve(outer.map(([r, y]) => new THREE.Vector2(r, y))).getPoints(40);
    // Bottom to top, or the surface faces inwards and you see through to the far side.
    const points = [new THREE.Vector2(.001, outer[0][1]), ...edge, new THREE.Vector2(.001, outer.at(-1)[1])].reverse();
    const mesh = new THREE.Mesh(new THREE.LatheGeometry(points, 48), material(color, .62));
    mesh.scale.z = depth;
    mesh.castShadow = true;
    whole.add(mesh);
  };
  lathe([[.2, 1.26], [.44, 1.21], [.55, 1.11], [.53, .95], [.41, .78], [.31, .62]], TOP);
  lathe([[.32, .66], [.34, .58], [.31, .5], [.18, .45]], TROUSERS, .72);
  whole.add(ball(SKIN, .15, 0, 1.3, 0, [1, 1.05, .95]));
  const neckband = new THREE.Mesh(new THREE.TorusGeometry(.17, .024, 10, 36), material(COLLAR, .6));
  neckband.rotation.x = Math.PI / 2 - .12;
  neckband.position.set(0, 1.25, .012);
  whole.add(neckband);
  // Chest and abs under the shirt.
  for (const side of [-1, 1]) {
    whole.add(ball(TOP, 1, side * .215, 1.03, .225, [.225, .125, .07], .58));
    whole.add(ball(TOP, 1, side * .52, 1.12, 0, [.15, .15, .15], .58));  // shoulders
    for (const [y, z] of [[.885, .235], [.81, .208], [.735, .178]]) whole.add(ball(TOP, 1, side * .08, y, z, [.07, .05, .042], .58));
  }

  const limb = color => { const mesh = new THREE.Mesh(new THREE.BufferGeometry(), material(color, .72)); mesh.castShadow = true; whole.add(mesh); return mesh; };
  const armL = limb(SKIN), armR = limb(SKIN), sleeveL = limb(TOP), sleeveR = limb(TOP), legL = limb(TROUSERS), legR = limb(TROUSERS);
  const handFor = side => {
    const hand = new THREE.Group();
    hand.add(ball(SKIN, .1, 0, 0, 0, [1, 1.06, .85]), ball(SKIN, .036, side * -.085, .03, .04, [1, 1.35, 1]));
    whole.add(hand);
    return hand;
  };
  const handL = handFor(-1), handR = handFor(1);
  const bulge = (color, sizes) => { const mesh = ball(color, 1, 0, 0, 0, sizes, .6); whole.add(mesh); return mesh; };
  const muscles = {bicepL:bulge(SKIN, [.118, .23, .118]), bicepR:bulge(SKIN, [.118, .23, .118]), forearmL:bulge(SKIN, [.098, .21, .098]), forearmR:bulge(SKIN, [.098, .21, .098]),
                   thighL:bulge(TROUSERS, [.135, .25, .135]), thighR:bulge(TROUSERS, [.135, .25, .135]), calfL:bulge(TROUSERS, [.106, .18, .106]), calfR:bulge(TROUSERS, [.106, .18, .106])};
  const shoeFor = side => {
    const shoe = new THREE.Group();
    shoe.add(ball(SHOE, .1, 0, .01, .1, [1.05, .62, 1.75], .3), ball('#e9e5dc', .1, 0, -.035, .1, [1.1, .22, 1.8], .5));
    shoe.rotation.y = side * .16;
    whole.add(shoe);
    return shoe;
  };
  const shoeL = shoeFor(-1), shoeR = shoeFor(1);

  return {whole, head, eyeL, eyeR, browL, browR, nose, cheekL, cheekR, lips, mouth, teeth, tongue, lick, sweat, shades, armL, armR, sleeveL, sleeveR, muscles, legL, legR, handL, handR, shoeL, shoeR};
}

function tube(mesh, from, to, bend, radius) {
  const middle = new THREE.Vector3().lerpVectors(from, to, .5);
  const across = new THREE.Vector3(to.y - from.y, from.x - to.x, 0).normalize().multiplyScalar(bend);
  middle.add(across);
  const curve = new THREE.QuadraticBezierCurve3(from.clone(), middle, to.clone());  // copies: the caller reuses its vectors
  mesh.geometry.dispose();
  mesh.geometry = new THREE.TubeGeometry(curve, 14, radius, 12);
  return curve;
}

const UP = new THREE.Vector3(0, 1, 0);
function along(mesh, curve, t) {
  curve.getPoint(t, mesh.position);
  mesh.quaternion.setFromUnitVectors(UP, curve.getTangent(t));
}
function sleeve(mesh, curve) {
  const part = new THREE.CatmullRomCurve3([0, .1, .2, .3].map(t => curve.getPoint(t)));
  mesh.geometry.dispose();
  mesh.geometry = new THREE.TubeGeometry(part, 8, .122, 14);
}

function start() {
  const button = document.getElementById('mic'), flatDrawing = document.getElementById('face');
  const renderer = new THREE.WebGLRenderer({antialias:true, alpha:true, powerPreference:'low-power'});
  const canvas = renderer.domElement;
  canvas.id = 'buddy3d';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.style.cssText = 'position:absolute;inset:-25%;width:150%;height:150%;pointer-events:none';
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.08;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(FIELD_OF_VIEW, 1, 4, 30);
  const distance = VIEW_HEIGHT / 2 / Math.tan(degrees(FIELD_OF_VIEW / 2));
  camera.position.set(0, 2.2, distance);
  camera.lookAt(0, 1.22, 0);

  scene.add(new THREE.HemisphereLight('#fff4e2', '#6f5a7a', 1.5));
  const key = new THREE.DirectionalLight('#fff1dc', 2.5);
  key.position.set(-2.4, 5, 5);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.left = key.shadow.camera.bottom = -2.4;
  key.shadow.camera.right = key.shadow.camera.top = 2.8;
  key.shadow.radius = 7;
  key.shadow.bias = -.0006;
  key.shadow.normalBias = .02;
  scene.add(key);
  // Two lights from behind, or his dark hair and shoes would vanish into the black page.
  const rim = new THREE.DirectionalLight('#bcd4ff', 3.2);
  rim.position.set(3.2, 2.6, -3.5);
  const rimLeft = new THREE.DirectionalLight('#ffe2c4', 2.4);
  rimLeft.position.set(-3.4, 1.8, -3.2);
  scene.add(rim, rimLeft);

  const stage = new THREE.Group();
  scene.add(stage);
  const buddy = buildBuddy();
  stage.add(buddy.whole);

  const banana = arc([[-.17, .06, 0], [0, -.015, .05], [.17, .08, 0]], .038, '#ffe14d');
  banana.position.set(bodyX(92), .03, .55);
  stage.add(banana);
  const stars = [0, 1, 2].map(() => { const star = sprite('★', '#ffe14d', .3); scene.add(star); return star; });
  const snore = sprite('z', '#f7f1ea', .36), notes = sprite('♪', '#f7f1ea', .36), thought = sprite('?', '#f7f1ea', .5);
  scene.add(snore, notes, thought);

  const resize = () => {
    const size = button.clientWidth * 1.5;
    if (!size) return;
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(size, size, false);
  };
  new ResizeObserver(resize).observe(button);
  resize();

  button.insertBefore(canvas, button.firstChild);
  flatDrawing.style.visibility = 'hidden';
  canvas.addEventListener('webglcontextlost', () => { canvas.remove(); flatDrawing.style.visibility = ''; running = false; });

  const shoulderL = new THREE.Vector3(), shoulderR = new THREE.Vector3(), target = new THREE.Vector3(), other = new THREE.Vector3();
  const spots = {turn:0, shades:0};
  let running = true;

  function posedEye(eye, side, now) {
    const dizzy = now < dizzyUntil, phase = side === 'L' ? 0 : 3;
    const r = eye.radius;
    const px = toon.lookX * r * .42 + (dizzy ? Math.cos(now / 90 + phase) * r * .4 : 0);
    const py = -toon.lookY * r * .4 + (dizzy ? Math.sin(now / 90 + phase) * r * .4 : 0);
    eye.pupil.position.set(px, py, Math.sqrt(Math.max(.01, r * r - px * px - py * py)) * .96);
    const winking = side === 'R' && now < winkUntil;
    const open = Math.max(0, Math.min(1, winking ? 0 : toon.open * (1 - toon.shut)));
    eye.lid.rotation.x = mix(1.55, -.55, open);
    eye.group.scale.setScalar(1 + toon.pop * .3);
    const smiling = toon.happy > .5 && !winking, shut = !smiling && open < .14;
    eye.inside.visible = !smiling && !shut;
    eye.shut.visible = shut;
    eye.smile.visible = smiling;
  }

  function posedMouth(now) {
    const cx = headX(150 + toon.lookX * 3 + toon.side * 9), cy = headY(156 + toon.lookY * 1.5);
    const half = (15 + toon.wide * 27) / 100, gape = toon.mouthOpen * .29, curve = toon.smile * .13;
    const leftY = cy + curve * .5 - toon.side * .07, rightY = cy + curve * .5 + toon.side * .07;
    const quadratic = (a, control, b, t) => (1 - t) * (1 - t) * a + 2 * t * (1 - t) * control + t * t * b;
    const top = t => quadratic(leftY, cy - curve + gape * .28, rightY, t);
    const bottom = t => quadratic(leftY, cy - curve * 1.3 - gape, rightY, t);
    const across = t => cx - half + half * 2 * t;
    // Lips all the way round, so a resting mouth is still a clear smile and not a hairline.
    buddy.lips.draw(t => cx - half - .022 + (half + .022) * 2 * t, t => top(t) + .022, t => bottom(t) - .022, .009);
    buddy.mouth.draw(across, top, bottom, .012);
    const showTeeth = toon.smile > .3 || gape > .07;
    buddy.teeth.mesh.visible = showTeeth;
    if (showTeeth) buddy.teeth.draw(across, t => top(t) - .004, t => Math.max(bottom(t) + .004, top(t) - .1), .017);
    buddy.tongue.mesh.visible = gape > .1;
    if (gape > .1) {
      const width = Math.min(half * .62, .17);
      const inMouth = t => (cx + (t - .5) * 2 * width - across(0)) / (half * 2);
      const edge = t => 1 - Math.pow(2 * t - 1, 2);
      buddy.tongue.draw(t => cx + (t - .5) * 2 * width, t => mix(bottom(inMouth(t)) + .004, bottom(.5) + gape * .36, edge(t)), t => bottom(inMouth(t)) + .004, .02);
    }
    buddy.lick.visible = toon.lick > .4;
    buddy.lick.position.set(cx + .08, cy - curve * .55 - gape * .3 - .13, headSurface(cx + .08, cy - .15) + .03);
  }

  function draw(now) {
    if (!running) return;
    requestAnimationFrame(draw);
    const sleeping = document.body.dataset.state === 'sleeping';
    const breathe = calm ? 0 : Math.sin(now / (sleeping ? 1700 : 900)) * (sleeping ? 1.5 : 1.8);
    const drift = calm ? 0 : Math.sin(now / 2600) * .05;
    const { whole, head, eyeL, eyeR } = buddy;

    whole.position.set(toon.sway / 100, toon.lift / 100, 0);
    whole.rotation.set(0, toon.lookX * .16 + drift, degrees(-toon.lean));
    whole.scale.set(1 + toon.squash * .14, 1 - toon.squash * .16 + breathe * .004, 1 + toon.squash * .14);
    stage.rotation.y = spots.turn;
    head.position.y = NECK_Y + breathe * .006;
    head.scale.setScalar(HEAD_SCALE);
    head.rotation.set(toon.lookY * .17, toon.lookX * .36 + drift * .7, degrees(-toon.tilt));

    // Down from his forehead when the screen gets bright.
    spots.shades += ((now < shadesUntil ? 1 : 0) - spots.shades) * .14;
    buddy.shades.visible = spots.shades > .02;
    buddy.shades.position.y = (1 - spots.shades) * .45;
    posedEye(eyeL, 'L', now); posedEye(eyeR, 'R', now);
    buddy.browL.position.set(headX(121), headY(60 + toon.browL), headSurface(headX(121), headY(60 + toon.browL)) + .03);
    buddy.browR.position.set(headX(180), headY(56 + toon.browR), headSurface(headX(180), headY(56 + toon.browR)) + .03);
    buddy.browL.rotation.z = degrees(toon.worry * 20 + toon.smile * 3 + toon.lookX * 2);
    buddy.browR.rotation.z = degrees(toon.worry * -20 - toon.smile * 3 + toon.lookX * 2);
    buddy.nose.position.x = headX(150 + toon.lookX * 4);
    buddy.nose.position.y = headY(124 + toon.lookY * 2 - toon.worry * .8);
    buddy.cheekL.material.opacity = buddy.cheekR.material.opacity = toon.cheek;
    posedMouth(now);
    buddy.sweat.visible = toon.sweat > .3;
    buddy.sweat.position.y = headY(66 + (now / 18) % 14);

    const reach = (hand, x, y, turn) => {
      const hx = bodyX(x), hy = bodyY(y);
      // Over the face a hand comes forward, or it would vanish into his head.
      const faceX = hx / HEAD_SCALE, faceY = (hy - NECK_Y) / HEAD_SCALE;
      const across = faceX / (HEAD.x + .1), up = (faceY - HEAD_Y) / (HEAD.y + .1);
      const onFace = across * across + up * up < 1;
      const z = onFace ? headSurface(faceX, faceY) * HEAD_SCALE + .16 : hy > .82 ? .38 : .3;
      hand.position.set(hx, hy, z);
      hand.rotation.z = degrees(turn);
    };
    reach(buddy.handL, toon.handLx, toon.handLy, toon.handLy < 180 ? 170 : 0);
    reach(buddy.handR, toon.handRx, toon.handRy, toon.handRy < 180 ? -170 : 0);
    shoulderL.set(-.53, 1.1, .02); shoulderR.set(.53, 1.1, .02);
    const armL = tube(buddy.armL, shoulderL, buddy.handL.position, -.16, .086), armR = tube(buddy.armR, shoulderR, buddy.handR.position, .16, .086);
    sleeve(buddy.sleeveL, armL); sleeve(buddy.sleeveR, armR);
    along(buddy.muscles.bicepL, armL, .4); along(buddy.muscles.bicepR, armR, .4);
    along(buddy.muscles.forearmL, armL, .74); along(buddy.muscles.forearmR, armR, .74);

    const heelL = bodyX(126 - toon.footL * .15), heelR = bodyX(174 + toon.footR * .15);
    const footL = bodyY(276 - toon.footL), footR = bodyY(276 - toon.footR);
    buddy.shoeL.position.set(heelL - .05, footL, 0); buddy.shoeR.position.set(heelR + .05, footR, 0);
    buddy.shoeL.rotation.x = degrees(-toon.footL * .7); buddy.shoeR.rotation.x = degrees(-toon.footR * .7);
    const legL = tube(buddy.legL, target.set(-.17, .55, 0), other.set(heelL, footL + .03, .01), -.02, .092), legR = tube(buddy.legR, target.set(.17, .55, 0), other.set(heelR, footR + .03, .01), .02, .092);
    along(buddy.muscles.thighL, legL, .24); along(buddy.muscles.thighR, legR, .24);
    along(buddy.muscles.calfL, legL, .72); along(buddy.muscles.calfR, legR, .72);

    banana.visible = toon.banana > .5;
    stars.forEach((star, index) => {
      star.visible = toon.stars > .4;
      const angle = now / 260 + index * 2.09;
      star.position.set(Math.cos(angle) * .56, 2.66 + Math.sin(angle) * .1, Math.sin(angle) * .5);
    });
    snore.visible = toon.snore > .3;
    snore.position.set(.62 + Math.sin(now / 900) * .05, 2.5 + (calm ? 0 : Math.abs(Math.sin(now / 1500)) * .22), .3);
    notes.visible = toon.notes > .3;
    notes.position.set(-.96 + Math.sin(now / 240) * .07, 2.3 + (calm ? 0 : (now / 30) % 18 / 100), .3);
    thought.visible = toon.thought > .35;
    thought.position.set(.68, 2.5 + (calm ? 0 : Math.sin(now / 300) * .03), .3);
    renderer.render(scene, camera);
  }
  window.buddy3d = spots;  // the demo video turns the stage with this
  requestAnimationFrame(draw);
}

try {
  start();
} catch (error) {
  console.warn('Buddy stays flat:', error);
}
