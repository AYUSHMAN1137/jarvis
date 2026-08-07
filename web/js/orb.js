/* ═══════════════════════════════════════════════════════════════════
   Multi-State WebGL Orb Renderer
   ───────────────────────────────────────────────────────────────────
   6 animated states, each with its own visual personality.
   Smooth lerp transitions between states (~250ms settle time).
   ═══════════════════════════════════════════════════════════════════ */

/* ── State presets ──
 * Each key maps to a set of target values for shader uniforms.
 * The render loop lerps the current values toward these targets.
 *
 *   hover      – 0=dim/inactive, 1=full brightness
 *   speedMul   – time multiplier for noise + angular blend
 *   noiseMul   – surface wobble intensity (0=smooth sphere, >1=chaotic)
 *   hue        – hue rotation in degrees from base purple-cyan palette
 *   glowMul    – overall glow/brightness multiplier
 *   waveAmp    – UV waveform distortion (listening "audio response" effect)
 *   orbitSpeed – orbiting highlight speed multiplier
 *   rotSpeed   – continuous rotation speed (rad/s)
 */
const ORB_DEFAULTS = {
    idle:      { hover: 0,   speedMul: 0.3, noiseMul: 0.6, hue: 0,   glowMul: 0.5, waveAmp: 0.0,  orbitSpeed: 1.0, rotSpeed: 0.0  },
    listening: { hover: 1,   speedMul: 1.5, noiseMul: 0.8, hue: 60,  glowMul: 0.9, waveAmp: 0.25, orbitSpeed: 1.0, rotSpeed: 0.15 },
    thinking:  { hover: 1,   speedMul: 2.0, noiseMul: 1.4, hue: -20, glowMul: 1.0, waveAmp: 0.05, orbitSpeed: 2.5, rotSpeed: 0.6  },
    searching: { hover: 1,   speedMul: 1.8, noiseMul: 1.0, hue: 90,  glowMul: 0.8, waveAmp: 0.0,  orbitSpeed: 3.0, rotSpeed: 0.4  },
    working:   { hover: 1,   speedMul: 1.2, noiseMul: 1.2, hue: 30,  glowMul: 1.2, waveAmp: 0.03, orbitSpeed: 1.5, rotSpeed: 0.3  },
    speaking:  { hover: 1,   speedMul: 0.8, noiseMul: 0.7, hue: 0,   glowMul: 0.7, waveAmp: 0.0,  orbitSpeed: 1.0, rotSpeed: 0.2  },
};

// Mutable copy — dashboard modifies this, ORB_DEFAULTS stays immutable for reset
const ORB_STATES = JSON.parse(JSON.stringify(ORB_DEFAULTS));

/* ---------------------------------------------------------------------------
 * State configuration API.  [M14 P9.1]
 *
 * ORB_STATES was a mutable `let` and the dashboard reassigned the whole thing
 * from script.js. That worked only because both files are classic scripts
 * sharing one global scope. An imported binding is read-only, so the same line
 * throws under ES modules - and it was the wrong shape regardless: the orb
 * should own its state table and expose intent, not hand out a variable.
 *
 * getStateConfig returns a COPY on purpose. The dashboard reads a config,
 * mutates it while a slider is dragged and writes it back; handing out the
 * live object would mean "Reset Defaults" has nothing left to reset to,
 * because the defaults would have been edited in place.
 * ------------------------------------------------------------------------- */
export function getStateConfig(name) {
    const preset = ORB_STATES[name];
    return preset ? { ...preset } : null;
}

export function setStateConfig(name, patch) {
    if (!ORB_STATES[name]) return false;
    Object.assign(ORB_STATES[name], patch);
    return true;
}

export function replaceStateConfigs(all) {
    if (!all) return;
    for (const k of Object.keys(ORB_STATES)) {
        if (all[k]) Object.assign(ORB_STATES[k], all[k]);
    }
}

export function resetStateConfigs() {
    // Mutates in place rather than reassigning, so that every closure already
    // holding the table keeps seeing the same object.
    for (const k of Object.keys(ORB_STATES)) {
        for (const prop of Object.keys(ORB_STATES[k])) delete ORB_STATES[k][prop];
        Object.assign(ORB_STATES[k], ORB_DEFAULTS[k]);
    }
}

export function snapshotStateConfigs() {
    return JSON.parse(JSON.stringify(ORB_STATES));
}

export function stateNames() {
    return Object.keys(ORB_STATES);
}

/* ---------------------------------------------------------------------------
 * State change observer.  [M14 P9.2]
 *
 * Replaces script.js monkey-patching setState / setStateInstant to update the
 * status badge. A listener is explicit, survives the orb being constructed
 * differently, and lets more than one thing watch the state.
 *
 * Listeners are wrapped in try/catch: a throwing badge updater must not stop
 * the orb from changing state (CLAUDE.md rule #6).
 * ------------------------------------------------------------------------- */
const orbStateListeners = new Set();

export function onOrbStateChange(fn) {
    orbStateListeners.add(fn);
    return () => orbStateListeners.delete(fn);
}

function emitOrbState(name) {
    for (const fn of orbStateListeners) {
        try { fn(name); } catch (e) { console.warn('[orb] state listener threw', e); }
    }
}

// Default lerp rate (instance property, overridable from dashboard)
const ORB_DEFAULT_LERP_RATE = 6;

export class OrbRenderer {
    constructor(container, opts = {}) {
        this.container = container;
        this.baseHue = opts.hue ?? 0;
        this.hoverIntensity = opts.hoverIntensity ?? 0.2;
        this.bgColor = opts.backgroundColor ?? [0.02, 0.02, 0.06];
        this.lerpRate = ORB_DEFAULT_LERP_RATE;

        // ── State management ──
        this.stateName = 'idle';
        const idle = ORB_STATES.idle;

        // Animated properties — current values (lerped toward targets each frame)
        this.targetHover      = idle.hover;       this.currentHover      = idle.hover;
        this.targetSpeedMul   = idle.speedMul;    this.currentSpeedMul   = idle.speedMul;
        this.targetNoiseMul   = idle.noiseMul;    this.currentNoiseMul   = idle.noiseMul;
        this.targetHueShift   = idle.hue;         this.currentHueShift   = idle.hue;
        this.targetGlowMul    = idle.glowMul;     this.currentGlowMul    = idle.glowMul;
        this.targetWaveAmp    = idle.waveAmp;     this.currentWaveAmp    = idle.waveAmp;
        this.targetOrbitSpeed = idle.orbitSpeed;   this.currentOrbitSpeed = idle.orbitSpeed;
        this.targetRotSpeed   = idle.rotSpeed;    this.currentRotSpeed   = idle.rotSpeed;

        this.currentRot = 0;
        this.lastTs = 0;

        this.canvas = document.createElement('canvas');
        this.canvas.style.width = '100%';
        this.canvas.style.height = '100%';
        this.container.appendChild(this.canvas);
        this.gl = this.canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false, antialias: false });
        if (!this.gl) { console.warn('WebGL not available'); return; }
        // ── Render quality knobs ──  [M14 P2.3a / P2.3d]
        // maxDpr caps the backing store; renderScale is moved by the adaptive
        // controller in _adapt(). Both are read by _resize().
        this.maxDpr      = opts.maxDpr ?? 1.25;
        this.renderScale = 1.0;

        this._build();
        this._resize();
        this._onResize = this._resize.bind(this);
        window.addEventListener('resize', this._onResize);
        this._raf = requestAnimationFrame(this._loop.bind(this));

        /* ── Visibility gating ──  [M14 P2.3b]
         * Browsers throttle rAF in a hidden tab but do not stop it, and this app
         * normally lives in a desktop window sitting behind other windows.
         * Fully stopping the loop is the difference between ~0% and several
         * percent of a core on a machine that is also running an embedding
         * model, FAISS and a UIA COM thread. */
        this._onVisibility = () => {
            if (document.hidden) this._pause('hidden');
            else this._resume('hidden');
        };
        document.addEventListener('visibilitychange', this._onVisibility);

        /* ── Off-screen gating ──  [M14 P2.3c]
         * The orb is position:fixed and centred so it is almost always
         * intersecting, but this covers a very short window and costs nothing
         * when it never fires. Occlusion by an opaque panel is a separate thing
         * with no browser API - see setOccluded(). */
        try {
            this._io = new IntersectionObserver((entries) => {
                for (const e of entries) {
                    if (e.isIntersecting) this._resume('offscreen');
                    else this._pause('offscreen');
                }
            }, { threshold: 0 });
            this._io.observe(this.container);
        } catch (_) { /* no IntersectionObserver - never pauses for this reason */ }

        /* ── Reduced motion ──  [M14 P2.3e]
         * CSS cannot stop a WebGL loop, so honour the preference here. The orb
         * stays visible with its per-state colour (that is information - it says
         * what JARVIS is doing) but renders as a still image. */
        try {
            this._mqMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
            this._onMotionPref = () => {
                if (this._mqMotion.matches) {
                    this._renderOnce();
                    this._pause('reduced-motion');
                } else {
                    this._resume('reduced-motion');
                }
            };
            this._mqMotion.addEventListener('change', this._onMotionPref);
            if (this._mqMotion.matches) {
                // Defer so _build()/_resize() have completed.
                requestAnimationFrame(() => this._onMotionPref());
            }
        } catch (_) { /* no matchMedia - orb animates as before */ }
    }

    /* ── Pause / resume with reason tracking ──  [M14 P2.3]
     * Three independent things can pause the orb: the tab going hidden, the orb
     * being covered by an opaque panel, and the user preferring reduced motion.
     * A single boolean would let whichever one resumed last cancel the others,
     * so pauses are tracked as a set of reasons and the loop only restarts when
     * the set is empty. */
    _pause(reason) {
        this._pauseReasons ??= new Set();
        this._pauseReasons.add(reason);
        if (this._raf) {
            cancelAnimationFrame(this._raf);
            this._raf = null;
        }
    }

    _resume(reason) {
        this._pauseReasons ??= new Set();
        this._pauseReasons.delete(reason);
        if (this._pauseReasons.size === 0 && !this._raf) {
            /* Reset lastTs before restarting. _loop computes
             *   dt = this.lastTs ? t - this.lastTs : 0.016
             * so after a five-minute pause dt would be 300: alpha clamps to 1 and
             * every lerped property snaps, while currentRot jumps by hundreds of
             * radians in a single frame. Zeroing lastTs makes the first frame use
             * the 0.016 default instead. Do not remove this line. */
            this.lastTs = 0;
            this._raf = requestAnimationFrame(this._loop.bind(this));
        }
    }

    get paused() {
        return !!(this._pauseReasons && this._pauseReasons.size);
    }

    /* ── setOccluded: caller reports the orb is fully hidden behind UI ──
     * There is no browser API for "is this element visually covered", so the
     * panels that cover it say so. The orb dashboard deliberately does NOT call
     * this - live preview is the entire point of that panel. */
    setOccluded(occluded) {
        if (occluded) this._pause('occluded');
        else this._resume('occluded');
    }

    /* ── Adaptive quality ──  [M14 P2.3d]
     * This machine also runs the FastAPI server, a sentence-transformers model,
     * FAISS, a UIA COM thread and a file indexer. How much GPU is left over is
     * not knowable up front and changes minute to minute, so a fixed quality
     * setting is the wrong tool. Watch a rolling average of frame cost, step
     * renderScale down when consistently missing frames and back up when
     * consistently comfortable. Different up/down thresholds plus a cooldown
     * stop it oscillating.
     *
     * Deliberately not exposed in the orb dashboard: it is a safety mechanism,
     * not a preference. Size and Speed sliders already exist for taste. */
    _adapt(dt) {
        const ms = dt * 1000;
        this._frameAvg = this._frameAvg == null ? ms : this._frameAvg * 0.9 + ms * 0.1;
        this._adaptCooldown = (this._adaptCooldown ?? 0) - dt;
        if (this._adaptCooldown > 0) return;

        const SCALE_MIN = 0.5;
        const SCALE_MAX = 1.0;
        const SCALE_STEP = 0.125;

        if (this._frameAvg > 22 && this.renderScale > SCALE_MIN) {
            // Consistently below ~45fps: shed pixels.
            this.renderScale = Math.max(SCALE_MIN, this.renderScale - SCALE_STEP);
            this._resize();
            this._adaptCooldown = 3;   // seconds; _resize reallocates the backing
        } else if (this._frameAvg < 13 && this.renderScale < SCALE_MAX) {
            // Comfortably above 75fps: we can afford to look better.
            this.renderScale = Math.min(SCALE_MAX, this.renderScale + SCALE_STEP);
            this._resize();
            this._adaptCooldown = 8;   // slower to upgrade than to downgrade
        }
    }

    /* ── _lerpSnap: assign every current* from its target* immediately ── */
    _lerpSnap() {
        this.currentHover      = this.targetHover;
        this.currentSpeedMul   = this.targetSpeedMul;
        this.currentNoiseMul   = this.targetNoiseMul;
        this.currentHueShift   = this.targetHueShift;
        this.currentGlowMul    = this.targetGlowMul;
        this.currentWaveAmp    = this.targetWaveAmp;
        this.currentOrbitSpeed = this.targetOrbitSpeed;
        this.currentRotSpeed   = this.targetRotSpeed;
    }

    /* ── _renderOnce: draw exactly one frame at the current values ──
     * Used when reduced-motion is on (still image) and when a state changes
     * while paused, so the orb's colour still reports what JARVIS is doing even
     * though it is not animating. */
    _renderOnce() {
        if (!this.pgm) return;
        this._draw(this.lastTs || 0);
    }

    static VERT = `
    precision highp float;
    attribute vec2 position;
    attribute vec2 uv;
    varying vec2 vUv;
    void main(){ vUv=uv; gl_Position=vec4(position,0.0,1.0); }`;

    static FRAG = `
    precision highp float;
    uniform float iTime;
    uniform vec3  iResolution;
    uniform float hue;
    uniform float hover;
    uniform float rot;
    uniform float hoverIntensity;
    uniform vec3  backgroundColor;
    /* ── New state-driven uniforms ── */
    uniform float speedMul;
    uniform float noiseMul;
    uniform float glowMul;
    uniform float waveAmp;
    uniform float orbitSpeed;
    varying vec2  vUv;

    /* ----- Color-space conversion: RGB ↔ YIQ ----- */
    // YIQ is the color model used by NTSC television. Converting to
    // YIQ lets us rotate the hue of any color by simply rotating the
    // I and Q components, then converting back to RGB.
    vec3 rgb2yiq(vec3 c){float y=dot(c,vec3(.299,.587,.114));float i=dot(c,vec3(.596,-.274,-.322));float q=dot(c,vec3(.211,-.523,.312));return vec3(y,i,q);}
    vec3 yiq2rgb(vec3 c){return vec3(c.x+.956*c.y+.621*c.z,c.x-.272*c.y-.647*c.z,c.x-1.106*c.y+1.703*c.z);}
    // adjustHue: rotate a color's hue by 'hueDeg' degrees.
    // 1. Convert RGB → YIQ.
    // 2. Rotate the (I, Q) pair by the hue angle (2D rotation matrix).
    // 3. Convert YIQ → RGB.
    vec3 adjustHue(vec3 color,float hueDeg){float h=hueDeg*3.14159265/180.0;vec3 yiq=rgb2yiq(color);float cosA=cos(h);float sinA=sin(h);float i2=yiq.y*cosA-yiq.z*sinA;float q2=yiq.y*sinA+yiq.z*cosA;yiq.y=i2;yiq.z=q2;return yiq2rgb(yiq);}

    /* ----- 3D Simplex Noise (snoise3) ----- */
    // Simplex noise is a smooth, natural-looking pseudo-random function
    // invented by Ken Perlin. Given a 3D coordinate it returns a value
    // roughly in [-1, 1]. By feeding (uv, time) we get animated,
    // organic-looking variation that drives the orb's wobbly edge.
    //
    // hash33: a cheap hash that maps a vec3 to a pseudo-random vec3 in
    //         [-1, 1]. Used internally by the noise to create random
    //         gradient vectors at each lattice point.
    vec3 hash33(vec3 p3){p3=fract(p3*vec3(.1031,.11369,.13787));p3+=dot(p3,p3.yxz+19.19);return -1.0+2.0*fract(vec3(p3.x+p3.y,p3.x+p3.z,p3.y+p3.z)*p3.zyx);}

    // snoise3: the actual 3D simplex noise implementation.
    // K1 and K2 are the skew/unskew constants for a 3D simplex grid.
    // The function:
    //   1. Skews the input into simplex (tetrahedral) space.
    //   2. Determines which simplex cell the point falls in.
    //   3. Computes distance vectors to each of the cell's 4 corners.
    //   4. For each corner, evaluates a radial falloff kernel multiplied
    //      by the dot product of a pseudo-random gradient and the
    //      distance vector.
    //   5. Sums the contributions and scales to roughly [-1, 1].
    float snoise3(vec3 p){const float K1=.333333333;const float K2=.166666667;vec3 i=floor(p+(p.x+p.y+p.z)*K1);vec3 d0=p-(i-(i.x+i.y+i.z)*K2);vec3 e=step(vec3(0.0),d0-d0.yzx);vec3 i1=e*(1.0-e.zxy);vec3 i2=1.0-e.zxy*(1.0-e);vec3 d1=d0-(i1-K2);vec3 d2=d0-(i2-K1);vec3 d3=d0-0.5;vec4 h=max(0.6-vec4(dot(d0,d0),dot(d1,d1),dot(d2,d2),dot(d3,d3)),0.0);vec4 n=h*h*h*h*vec4(dot(d0,hash33(i)),dot(d1,hash33(i+i1)),dot(d2,hash33(i+i2)),dot(d3,hash33(i+1.0)));return dot(vec4(31.316),n);}

    // extractAlpha: the orb is rendered on a transparent background.
    // This helper takes an RGB color and derives an alpha from the
    // brightest channel. That way fully-black areas become transparent
    // and bright areas become opaque — giving us a soft-edged glow
    // without needing a separate alpha mask.
    vec4 extractAlpha(vec3 c){float a=max(max(c.r,c.g),c.b);return vec4(c/(a+1e-5),a);}

    /* ----- Palette & geometry constants ----- */
    // Three base colors that define the orb's purple-cyan palette.
    // They get hue-shifted at runtime by the 'hue' uniform.
    const vec3 baseColor1=vec3(.611765,.262745,.996078);   // vivid purple
    const vec3 baseColor2=vec3(.298039,.760784,.913725);   // cyan / teal
    const vec3 baseColor3=vec3(.062745,.078431,.600000);   // deep indigo

    const float innerRadius=0.6;   // normalized radius of the orb's inner core
    const float noiseScale=0.65;   // how zoomed-in the noise pattern is

    /* ----- Procedural light falloff helpers ----- */
    // light1: inverse-distance falloff  →  I / (1 + d·a)
    // light2: inverse-square falloff    →  I / (1 + d²·a)
    // 'i' = intensity, 'a' = attenuation, 'd' = distance.
    // These give the orb its glowing highlight spots.
    float light1(float i,float a,float d){return i/(1.0+d*a);}
    float light2(float i,float a,float d){return i/(1.0+d*d*a);}

    /* ----- draw(): the core orb rendering routine ----- */
    // Given a UV coordinate (centered, normalized so the short axis
    // spans -1 to 1), this function returns an RGBA color for that
    // pixel.
    //
    // Step-by-step:
    //   1. Hue-shift the three base colors.
    //   2. Convert the UV to polar-ish helpers (angle and length).
    //   3. Sample 3D simplex noise at (uv, time) to create organic,
    //      time-varying distortion.
    //   4. Compute a wobbly radius (r0) from the noise — this is what
    //      makes the edge of the orb undulate.
    //   5. Calculate multiple light/glow terms:
    //        v0 – main glow field (radial, noise-modulated)
    //        v1 – an orbiting highlight point
    //        v2, v3 – radial fade masks that confine color to the orb
    //   6. Blend the base colors using the angular position (cl) so
    //      the orb shifts between purple and cyan as you go around it.
    //   7. Compose a "dark" version and a "light" version of the orb,
    //      then blend between them based on background luminance so
    //      the orb looks good on both dark and light UIs.
    //   8. Pass the result through extractAlpha to get proper
    //      transparency for compositing.
    vec4 draw(vec2 uv){
        vec3 c1=adjustHue(baseColor1,hue);vec3 c2=adjustHue(baseColor2,hue);vec3 c3=adjustHue(baseColor3,hue);
        float ang=atan(uv.y,uv.x);float len=length(uv);float invLen=len>0.0?1.0/len:0.0;
        float bgLum=dot(backgroundColor,vec3(.299,.587,.114));  // perceptual luminance of the bg
        // ── speedMul drives noise evolution speed ──
        float n0=snoise3(vec3(uv*noiseScale,iTime*0.5*speedMul))*0.5+0.5;  // noise remapped to [0,1]
        // ── noiseMul scales the wobble amplitude (0.5=no wobble center) ──
        float nScaled=mix(0.5,n0,noiseMul);
        float r0=mix(mix(innerRadius,1.0,0.4),mix(innerRadius,1.0,0.6),nScaled);  // wobbly radius
        float d0=distance(uv,(r0*invLen)*uv);  // distance from pixel to the wobbly edge
        // ── glowMul amplifies the main glow ──
        float v0=light1(1.0*glowMul,10.0,d0);          // main radial glow
        v0*=smoothstep(r0*1.05,r0,len);        // hard-ish cutoff just outside the radius
        float innerFade=smoothstep(r0*0.8,r0*0.95,len);  // fade near the center
        v0*=mix(innerFade,1.0,bgLum*0.7);
        // ── speedMul also affects the angular color rotation ──
        float cl=cos(ang+iTime*2.0*speedMul)*0.5+0.5;  // angular color blend (rotates over time)
        // ── orbitSpeed controls the orbiting highlight ──
        float a2=iTime*-1.0*orbitSpeed;vec2 pos=vec2(cos(a2),sin(a2))*r0;float d=distance(uv,pos);  // orbiting light
        float v1=light2(1.5*glowMul,5.0,d);v1*=light1(1.0,50.0,d0);  // highlight with quick falloff
        float v2=smoothstep(1.0,mix(innerRadius,1.0,nScaled*0.5),len);  // outer fade mask
        float v3=smoothstep(innerRadius,mix(innerRadius,1.0,0.5),len);  // inner→outer ramp
        vec3 colBase=mix(c1,c2,cl);  // angular purple↔cyan blend
        float fadeAmt=mix(1.0,0.1,bgLum);
        // "dark" composite — used on dark backgrounds
        vec3 darkCol=mix(c3,colBase,v0);darkCol=(darkCol+v1)*v2*v3;darkCol=clamp(darkCol,0.0,1.0);
        // "light" composite — blends toward the background color
        vec3 lightCol=(colBase+v1)*mix(1.0,v2*v3,fadeAmt);lightCol=mix(backgroundColor,lightCol,v0);lightCol=clamp(lightCol,0.0,1.0);
        // final mix: lean toward lightCol when the background is bright
        vec3 fc=mix(darkCol,lightCol,bgLum);
        return extractAlpha(fc);
    }

    /* ----- mainImage(): entry point called by main() ----- */
    // Transforms the raw pixel coordinate into a centered, normalized
    // UV, applies rotation and the wavy hover distortion, then calls
    // draw().
    vec4 mainImage(vec2 fragCoord){
        vec2 center=iResolution.xy*0.5;float sz=min(iResolution.x,iResolution.y);
        vec2 uv=(fragCoord-center)/sz*2.0;  // center and normalize UV to [-1,1] on short axis
        // Apply 2D rotation (accumulated while the orb is "active")
        float s2=sin(rot);float c2=cos(rot);uv=vec2(c2*uv.x-s2*uv.y,s2*uv.x+c2*uv.y);
        // Wavy UV distortion driven by 'hover' (0→1 when active)
        uv.x+=hover*hoverIntensity*0.1*sin(uv.y*10.0+iTime);
        uv.y+=hover*hoverIntensity*0.1*sin(uv.x*10.0+iTime);
        // ── waveAmp: additional high-frequency waveform distortion (listening effect) ──
        uv.x+=waveAmp*sin(uv.y*20.0+iTime*4.0)*0.15;
        uv.y+=waveAmp*cos(uv.x*15.0+iTime*3.5)*0.1;
        return draw(uv);
    }

    /* ----- main(): GLSL entry point ----- */
    // Converts the varying vUv (0-1 range) back to pixel coordinates,
    // calls mainImage(), and writes the final pre-multiplied alpha
    // color to gl_FragColor.
    void main(){
        vec2 fc=vUv*iResolution.xy;vec4 col=mainImage(fc);
        gl_FragColor=vec4(col.rgb*col.a,col.a);
    }`;

    _compile(type, src) {
        const gl = this.gl;
        const s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
            console.error('Shader compile error:', gl.getShaderInfoLog(s));
            gl.deleteShader(s);
            return null;
        }
        return s;
    }

    _build() {
        const gl = this.gl;
        const vs = this._compile(gl.VERTEX_SHADER, OrbRenderer.VERT);
        const fs = this._compile(gl.FRAGMENT_SHADER, OrbRenderer.FRAG);
        if (!vs || !fs) return;

        this.pgm = gl.createProgram();
        gl.attachShader(this.pgm, vs);
        gl.attachShader(this.pgm, fs);
        gl.linkProgram(this.pgm);
        if (!gl.getProgramParameter(this.pgm, gl.LINK_STATUS)) {
            console.error('Program link error:', gl.getProgramInfoLog(this.pgm));
            return;
        }
        gl.useProgram(this.pgm);

        const posLoc = gl.getAttribLocation(this.pgm, 'position');
        const uvLoc  = gl.getAttribLocation(this.pgm, 'uv');

        const posBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
        gl.enableVertexAttribArray(posLoc);
        gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);

        const uvBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0,0, 2,0, 0,2]), gl.STATIC_DRAW);
        gl.enableVertexAttribArray(uvLoc);
        gl.vertexAttribPointer(uvLoc, 2, gl.FLOAT, false, 0, 0);

        this.u = {};
        [
            'iTime','iResolution','hue','hover','rot','hoverIntensity','backgroundColor',
            'speedMul','noiseMul','glowMul','waveAmp','orbitSpeed'
        ].forEach(name => {
            this.u[name] = gl.getUniformLocation(this.pgm, name);
        });

        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        gl.clearColor(0,0,0,0);
    }

    _resize() {
        /* Render-scale cap.  [M14 P2.3a]
         * The orb is a soft, noisy, blurred blob with no high-frequency detail
         * that benefits from a 2x or 3x device pixel ratio, but fragment shader
         * cost scales with the square of it: at DPR 1.5 an uncapped 600px orb is
         * 900x900 = 810k fragments of 3D simplex noise every frame.
         *
         * The shader normalises its UVs by min(iResolution.x, iResolution.y), so
         * changing the backing-store size does not change the orb's apparent
         * size or shape. Nothing else must compensate for this scale. */
        const dpr = Math.min(window.devicePixelRatio || 1, this.maxDpr) * this.renderScale;
        const w = this.container.clientWidth;
        const h = this.container.clientHeight;
        this.canvas.width  = Math.max(1, Math.round(w * dpr));
        this.canvas.height = Math.max(1, Math.round(h * dpr));
        if (this.gl) this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    }

    _loop(ts) {
        this._raf = requestAnimationFrame(this._loop.bind(this));
        if (!this.pgm) return;
        const t = ts * 0.001;
        const dt = this.lastTs ? t - this.lastTs : 0.016;
        this.lastTs = t;
        this._adapt(dt);   // [M14 P2.3d]
        this._lerp(dt);
        this._draw(t);
    }

    /* ── _lerp: advance every animated property toward its target ──
     * Split out of _loop in M14 P2.3 so _renderOnce() can draw without
     * advancing time, and so the loop body reads as three named steps. */
    _lerp(dt) {
        const alpha = Math.min(dt * this.lerpRate, 1);
        this.currentHover      += (this.targetHover      - this.currentHover)      * alpha;
        this.currentSpeedMul   += (this.targetSpeedMul   - this.currentSpeedMul)   * alpha;
        this.currentNoiseMul   += (this.targetNoiseMul   - this.currentNoiseMul)   * alpha;
        this.currentHueShift   += (this.targetHueShift   - this.currentHueShift)   * alpha;
        this.currentGlowMul    += (this.targetGlowMul    - this.currentGlowMul)    * alpha;
        this.currentWaveAmp    += (this.targetWaveAmp    - this.currentWaveAmp)     * alpha;
        this.currentOrbitSpeed += (this.targetOrbitSpeed  - this.currentOrbitSpeed) * alpha;
        this.currentRotSpeed   += (this.targetRotSpeed   - this.currentRotSpeed)   * alpha;

        // Accumulate rotation based on current rotation speed
        this.currentRot += dt * this.currentRotSpeed;
    }

    /* ── _draw: issue the GL calls for one frame at time t ── */
    _draw(t) {
        const gl = this.gl;
        if (!gl || !this.pgm) return;
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.useProgram(this.pgm);
        gl.uniform1f(this.u.iTime, t);
        gl.uniform3f(this.u.iResolution, this.canvas.width, this.canvas.height, this.canvas.width / this.canvas.height);
        gl.uniform1f(this.u.hue, this.baseHue + this.currentHueShift);
        gl.uniform1f(this.u.hover, this.currentHover);
        gl.uniform1f(this.u.rot, this.currentRot);
        gl.uniform1f(this.u.hoverIntensity, this.hoverIntensity);
        gl.uniform3f(this.u.backgroundColor, this.bgColor[0], this.bgColor[1], this.bgColor[2]);
        // ── New state-driven uniforms ──
        gl.uniform1f(this.u.speedMul, this.currentSpeedMul);
        gl.uniform1f(this.u.noiseMul, this.currentNoiseMul);
        gl.uniform1f(this.u.glowMul, this.currentGlowMul);
        gl.uniform1f(this.u.waveAmp, this.currentWaveAmp);
        gl.uniform1f(this.u.orbitSpeed, this.currentOrbitSpeed);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    /* ── setState: transition the orb to a named state ──
     * Valid states: idle, listening, thinking, searching, working, speaking
     * Transitions are smooth — the render loop lerps all properties.
     */
    setState(name) {
        const preset = ORB_STATES[name];
        if (!preset) {
            console.warn(`[Orb] Unknown state "${name}", ignoring.`);
            return;
        }
        this.stateName = name;
        this.targetHover      = preset.hover;
        this.targetSpeedMul   = preset.speedMul;
        this.targetNoiseMul   = preset.noiseMul;
        this.targetHueShift   = preset.hue;
        this.targetGlowMul    = preset.glowMul;
        this.targetWaveAmp    = preset.waveAmp;
        this.targetOrbitSpeed = preset.orbitSpeed;
        this.targetRotSpeed   = preset.rotSpeed;

        // Update CSS class on the container for per-state outer glow
        const ctn = this.container;
        // Remove all orb state classes
        ctn.classList.remove('active', 'speaking',
            'orb-idle', 'orb-listening', 'orb-thinking',
            'orb-searching', 'orb-working', 'orb-speaking');
        if (name !== 'idle') {
            ctn.classList.add('active');
        }
        ctn.classList.add('orb-' + name);

        /* If we are paused (reduced motion / hidden / occluded) the loop will
         * never pick this up, so snap and draw a single frame - the orb's colour
         * still has to report what JARVIS is doing.  [M14 P2.3] */
        if (this.paused) {
            this._lerpSnap();
            this._renderOnce();
        }

        emitOrbState(name);   // [M14 P9.2]
    }

    /* ── setStateInstant: jump to a state with zero lerp delay ──
     * Used by the dashboard for real-time preview. Sets BOTH current
     * and target values so the change is visible immediately.
     */
    setStateInstant(name) {
        const preset = ORB_STATES[name];
        if (!preset) return;
        this.stateName = name;

        // Set both current AND target — no lerp needed
        this.targetHover      = this.currentHover      = preset.hover;
        this.targetSpeedMul   = this.currentSpeedMul   = preset.speedMul;
        this.targetNoiseMul   = this.currentNoiseMul   = preset.noiseMul;
        this.targetHueShift   = this.currentHueShift   = preset.hue;
        this.targetGlowMul    = this.currentGlowMul    = preset.glowMul;
        this.targetWaveAmp    = this.currentWaveAmp     = preset.waveAmp;
        this.targetOrbitSpeed = this.currentOrbitSpeed  = preset.orbitSpeed;
        this.targetRotSpeed   = this.currentRotSpeed    = preset.rotSpeed;

        // Update CSS classes (only if not already on this state to avoid thrashing)
        const ctn = this.container;
        if (!ctn.classList.contains('orb-' + name)) {
            ctn.classList.remove('active', 'speaking',
                'orb-idle', 'orb-listening', 'orb-thinking',
                'orb-searching', 'orb-working', 'orb-speaking');
            if (name !== 'idle') ctn.classList.add('active');
            ctn.classList.add('orb-' + name);
        }

        // Same reasoning as setState(); _lerpSnap is a no-op here because this
        // method already wrote current* directly, but it keeps the two paths
        // symmetrical.  [M14 P2.3]
        if (this.paused) {
            this._lerpSnap();
            this._renderOnce();
        }

        emitOrbState(name);   // [M14 P9.2]
    }

    /* ── setProperty: update a single shader property instantly ──
     * Used during dashboard slider drag for real-time feedback.
     * No CSS class changes, no full state re-read — just one value.
     *   key: one of 'speedMul','noiseMul','glowMul','waveAmp','orbitSpeed','rotSpeed','hue','hover'
     */
    setProperty(key, value) {
        const propMap = {
            speedMul:   ['targetSpeedMul',   'currentSpeedMul'],
            noiseMul:   ['targetNoiseMul',   'currentNoiseMul'],
            glowMul:    ['targetGlowMul',    'currentGlowMul'],
            waveAmp:    ['targetWaveAmp',    'currentWaveAmp'],
            orbitSpeed: ['targetOrbitSpeed', 'currentOrbitSpeed'],
            rotSpeed:   ['targetRotSpeed',   'currentRotSpeed'],
            hue:        ['targetHueShift',   'currentHueShift'],
            hover:      ['targetHover',      'currentHover'],
        };
        const entry = propMap[key];
        if (!entry) return;
        this[entry[0]] = value;  // target
        this[entry[1]] = value;  // current (instant)
    }

    /* ── Backward compatibility: setActive maps to speaking/idle ── */
    setActive(active) {
        this.setState(active ? 'speaking' : 'idle');
    }

    /* ── applyGlobals: dashboard can update global params at runtime ── */
    applyGlobals(config) {
        if (config.lerpRate != null) this.lerpRate = config.lerpRate;
        if (config.baseHue != null) this.baseHue = config.baseHue;
        if (config.orbSize != null) {
            const sz = config.orbSize + 'px';
            this.container.style.width = sz;
            this.container.style.height = sz;
            this._resize();
        }
        if (config.idleOpacity != null) {
            // Update idle opacity CSS custom property
            this.container.style.setProperty('--orb-idle-opacity', config.idleOpacity);
        }
    }

    destroy() {
        if (this._raf) cancelAnimationFrame(this._raf);
        window.removeEventListener('resize', this._onResize);
        // Listeners and observers added by M14 P2.3 - a destroyed orb that is
        // still subscribed to visibilitychange keeps itself alive forever.
        if (this._onVisibility) document.removeEventListener('visibilitychange', this._onVisibility);
        if (this._io) this._io.disconnect();
        if (this._mqMotion && this._onMotionPref) {
            this._mqMotion.removeEventListener('change', this._onMotionPref);
        }
        if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
        const ext = this.gl && this.gl.getExtension('WEBGL_lose_context');
        if (ext) ext.loseContext();
    }
}
