/* ---------------------------------------------------------------------------
 * orbctl.js
 *
 * Orb lifecycle, the status badge, and the customization dashboard.
 *
 * Extracted from the single 4000-line script.js by /data/p9_3b.py.
 * [M14 P9.3]
 * ------------------------------------------------------------------------- */

import { EVENTS, emit } from './bus.js';
import { ORB_CONFIG_KEY, ORB_DEFAULT_GLOWS, ORB_SLIDER_MAP, ORB_STATE_LABELS } from './config.js';
import { $, el } from './dom.js';
import { historyDialogBackdrop } from './history.js';
import { OrbRenderer, getStateConfig, onOrbStateChange, replaceStateConfigs, resetStateConfigs, setStateConfig, snapshotStateConfigs } from './orb.js';
import { settingsPanel, updatePanelOverlay } from './panels.js';
import { state } from './state.js';
import { _hexToRgba } from './util.js';
export const statusDot    = document.querySelector('.status-dot');
export const statusText   = document.querySelector('.status-text');
export const orbContainer = $('orb-container');

// Runtime glow config (mutable, saved to localStorage)
export let orbGlows = JSON.parse(JSON.stringify(ORB_DEFAULT_GLOWS));

// Runtime global config


// Dashboard state
export let orbDashActive = false;
export let orbDashCurrentState = 'idle';
export const orbDashboard = $('orb-dashboard');
export const orbDashBtn   = $('orb-dashboard-btn');
export const orbDashClose = $('orb-dash-close');
export const orbDashReset = $('orb-dash-reset');
export const orbDashTabs  = $('orb-dash-tabs');

export function initOrbDashboard() {
    if (!orbDashboard) return;

    // Open/close dashboard
    if (orbDashBtn) {
        orbDashBtn.addEventListener('click', () => {
            orbDashActive = !orbDashActive;
            orbDashboard.classList.toggle('open', orbDashActive);
            orbDashboard.setAttribute('aria-hidden', !orbDashActive);
            updatePanelOverlay();
            if (orbDashActive) {
                orbDashSelectState('idle');
            } else {
                // Return orb to idle when closing
                if (state.orb) state.orb.setState('idle');
            }
        });
    }

    if (orbDashClose) {
        orbDashClose.addEventListener('click', () => {
            orbDashActive = false;
            orbDashboard.classList.remove('open');
            orbDashboard.setAttribute('aria-hidden', 'true');
            updatePanelOverlay();
            if (state.orb) state.orb.setState('idle');
        });
    }

    // Tab clicks
    if (orbDashTabs) {
        orbDashTabs.querySelectorAll('.orb-dash-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                orbDashSelectState(tab.dataset.state);
            });
        });
    }

    // Global sliders
    _orbDashBindGlobal('orb-g-lerp', 'lerpRate', v => v);
    _orbDashBindGlobal('orb-g-hue', 'baseHue', v => v + '°');
    _orbDashBindGlobal('orb-g-size', 'orbSize', v => v);
    _orbDashBindGlobal('orb-g-opacity', 'idleOpacity', v => v);

    // Per-state sliders
    Object.keys(ORB_SLIDER_MAP).forEach(name => {
        const el = $('orb-s-' + name);
        const valEl = $('orb-s-' + name + '-val');
        if (!el) return;
        el.addEventListener('input', () => {
            const v = parseFloat(el.value);
            const info = ORB_SLIDER_MAP[name];
            setStateConfig(orbDashCurrentState, { [info.key]: v });
            if (valEl) valEl.textContent = info.fmt(v);
            // Live preview: instant single-property update (no CSS thrashing)
            if (state.orb && orbDashActive) state.orb.setProperty(info.key, v);
            orbDashSave();
        });
    });

    // Glow color picker
    const glowColorEl = $('orb-s-glowcolor');
    const glowColorVal = $('orb-s-glowcolor-val');
    if (glowColorEl) {
        glowColorEl.addEventListener('input', () => {
            orbGlows[orbDashCurrentState].color = glowColorEl.value;
            if (glowColorVal) glowColorVal.textContent = glowColorEl.value;
            _orbDashApplyGlow(orbDashCurrentState);
            orbDashSave();
        });
    }

    // Glow size slider
    const glowSizeEl = $('orb-s-glowsize');
    const glowSizeVal = $('orb-s-glowsize-val');
    if (glowSizeEl) {
        glowSizeEl.addEventListener('input', () => {
            orbGlows[orbDashCurrentState].size = parseInt(glowSizeEl.value);
            if (glowSizeVal) glowSizeVal.textContent = glowSizeEl.value + 'px';
            _orbDashApplyGlow(orbDashCurrentState);
            orbDashSave();
        });
    }

    // Pulse speed slider
    const pulseEl = $('orb-s-pulse');
    const pulseVal = $('orb-s-pulse-val');
    if (pulseEl) {
        pulseEl.addEventListener('input', () => {
            orbGlows[orbDashCurrentState].pulse = parseFloat(pulseEl.value);
            if (pulseVal) pulseVal.textContent = parseFloat(pulseEl.value).toFixed(1) + 's';
            _orbDashApplyGlow(orbDashCurrentState);
            orbDashSave();
        });
    }

    // Reset defaults
    if (orbDashReset) {
        orbDashReset.addEventListener('click', () => {
            orbDashResetDefaults();
        });
    }
}

export function _orbDashBindGlobal(elId, key, fmt) {
    const el = $(elId);
    const valEl = $(elId + '-val');
    if (!el) return;
    el.addEventListener('input', () => {
        const v = parseFloat(el.value);
        state.orbGlobals[key] = v;
        if (valEl) valEl.textContent = fmt(v);
        if (state.orb) state.orb.applyGlobals({ [key]: v });
        orbDashSave();
    });
}

export function orbDashSelectState(stateName) {
    orbDashCurrentState = stateName;

    // Update tab active states
    if (orbDashTabs) {
        orbDashTabs.querySelectorAll('.orb-dash-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.state === stateName);
        });
    }

    // Force orb into preview state (instant — no lerp delay)
    if (state.orb && orbDashActive) state.orb.setStateInstant(stateName);

    // Populate slider values from the orb's own state table. This is a copy,
    // so editing it here cannot corrupt the defaults.  [M14 P9.1]
    const preset = getStateConfig(stateName);
    if (!preset) return;

    Object.keys(ORB_SLIDER_MAP).forEach(name => {
        const el = $('orb-s-' + name);
        const valEl = $('orb-s-' + name + '-val');
        const info = ORB_SLIDER_MAP[name];
        if (el) el.value = preset[info.key];
        if (valEl) valEl.textContent = info.fmt(preset[info.key]);
    });

    // Populate glow controls
    const glow = orbGlows[stateName] || ORB_DEFAULT_GLOWS[stateName];
    const glowColorEl = $('orb-s-glowcolor');
    const glowColorVal = $('orb-s-glowcolor-val');
    const glowSizeEl = $('orb-s-glowsize');
    const glowSizeVal = $('orb-s-glowsize-val');
    const pulseEl = $('orb-s-pulse');
    const pulseVal = $('orb-s-pulse-val');

    if (glowColorEl) { glowColorEl.value = glow.color; }
    if (glowColorVal) { glowColorVal.textContent = glow.color; }
    if (glowSizeEl) { glowSizeEl.value = glow.size; }
    if (glowSizeVal) { glowSizeVal.textContent = glow.size + 'px'; }
    if (pulseEl) { pulseEl.value = glow.pulse; }
    if (pulseVal) { pulseVal.textContent = glow.pulse.toFixed(1) + 's'; }
}

export function _orbDashApplyGlow(stateName) {
    // Dynamically update the CSS for this state's orb glow
    const glow = orbGlows[stateName];
    if (!glow || !orbContainer) return;

    // Build dynamic style rule for this state
    let styleEl = document.getElementById('orb-dash-dynamic-style');
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'orb-dash-dynamic-style';
        document.head.appendChild(styleEl);
    }

    // Rebuild all state glow rules
    let css = '';
    Object.keys(orbGlows).forEach(state => {
        const g = orbGlows[state];
        const rgba = _hexToRgba(g.color, 0.5);
        const rgbaLight = _hexToRgba(g.color, 0.2);
        const animRule = g.pulse > 0
            ? `animation: orbPulse ${g.pulse}s ease-in-out infinite;`
            : 'animation: none;';

        if (state === 'idle') {
            css += `#orb-container.orb-idle {
                opacity: var(--orb-idle-opacity, 0.35);
                ${animRule}
                filter: drop-shadow(0 0 ${g.size}px ${rgba});
            }\n`;
        } else {
            css += `#orb-container.orb-${state} {
                opacity: 1;
                ${animRule}
                filter: drop-shadow(0 0 ${g.size}px ${rgba})
                       drop-shadow(0 0 ${g.size * 2}px ${rgbaLight});
            }\n`;
        }
    });
    styleEl.textContent = css;
}

export function orbDashSave() {
    try {
        const config = {
            globals: { ...orbGlobals },
            states: snapshotStateConfigs(),
            glows: JSON.parse(JSON.stringify(orbGlows)),
        };
        localStorage.setItem(ORB_CONFIG_KEY, JSON.stringify(config));
    } catch (_) {}
}

export function orbDashLoad() {
    try {
        const raw = localStorage.getItem(ORB_CONFIG_KEY);
        if (!raw) return;
        const config = JSON.parse(raw);

        // Restore globals
        if (config.globals) {
            state.orbGlobals = { ...orbGlobals, ...config.globals };
            // Apply to orb instance if ready
            if (state.orb) state.orb.applyGlobals(state.orbGlobals);

            // Update global slider UI
            const gLerp = $('orb-g-lerp');
            const gHue = $('orb-g-hue');
            const gSize = $('orb-g-size');
            const gOpacity = $('orb-g-opacity');
            if (gLerp) { gLerp.value = state.orbGlobals.lerpRate; const v = $('orb-g-lerp-val'); if (v) v.textContent = state.orbGlobals.lerpRate; }
            if (gHue) { gHue.value = state.orbGlobals.baseHue; const v = $('orb-g-hue-val'); if (v) v.textContent = state.orbGlobals.baseHue + '°'; }
            if (gSize) { gSize.value = state.orbGlobals.orbSize; const v = $('orb-g-size-val'); if (v) v.textContent = state.orbGlobals.orbSize; }
            if (gOpacity) { gOpacity.value = state.orbGlobals.idleOpacity; const v = $('orb-g-opacity-val'); if (v) v.textContent = state.orbGlobals.idleOpacity; }
        }

        // Restore per-state presets
        if (config.states) {
            replaceStateConfigs(config.states);
        }

        // Restore glow settings
        if (config.glows) {
            Object.keys(config.glows).forEach(state => {
                if (orbGlows[state]) {
                    Object.assign(orbGlows[state], config.glows[state]);
                }
            });
            // Apply all glow rules
            _orbDashApplyGlow('idle');
        }
    } catch (e) {
        console.warn('[OrbDash] Failed to load config:', e);
    }
}

export function orbDashResetDefaults() {
    // Reset the orb's state table to its defaults.  [M14 P9.1]
    resetStateConfigs();
    // Reset glows
    orbGlows = JSON.parse(JSON.stringify(ORB_DEFAULT_GLOWS));
    // Reset globals
    state.orbGlobals = { lerpRate: 6, baseHue: 0, orbSize: 600, idleOpacity: 0.35 };

    // Apply to orb
    if (state.orb) {
        state.orb.applyGlobals(state.orbGlobals);
        state.orb.setStateInstant(orbDashCurrentState);
    }

    // Remove dynamic style
    const styleEl = document.getElementById('orb-dash-dynamic-style');
    if (styleEl) styleEl.textContent = '';

    // Remove orb container inline size override
    if (orbContainer) {
        orbContainer.style.width = '';
        orbContainer.style.height = '';
        orbContainer.style.removeProperty('--orb-idle-opacity');
    }

    // Clear localStorage
    try { localStorage.removeItem(ORB_CONFIG_KEY); } catch (_) {}

    // Refresh UI
    orbDashSelectState(orbDashCurrentState);

    // Reset global sliders
    const gLerp = $('orb-g-lerp'); if (gLerp) gLerp.value = 6;
    const gHue = $('orb-g-hue'); if (gHue) gHue.value = 0;
    const gSize = $('orb-g-size'); if (gSize) gSize.value = 600;
    const gOpacity = $('orb-g-opacity'); if (gOpacity) gOpacity.value = 0.35;
    const gLerpVal = $('orb-g-lerp-val'); if (gLerpVal) gLerpVal.textContent = '6';
    const gHueVal = $('orb-g-hue-val'); if (gHueVal) gHueVal.textContent = '0°';
    const gSizeVal = $('orb-g-size-val'); if (gSizeVal) gSizeVal.textContent = '600';
    const gOpacityVal = $('orb-g-opacity-val'); if (gOpacityVal) gOpacityVal.textContent = '0.35';
}

export function initOrb() {
    if (typeof OrbRenderer === 'undefined') return;
    try {
        state.orb = new OrbRenderer(orbContainer, {
            hue: 0,
            hoverIntensity: 0.3,
            backgroundColor: [0.02, 0.02, 0.06]
        });

        /* The status badge follows the orb through a listener instead of by
         * wrapping setState / setStateInstant. The old wrapping worked, but it
         * was invisible coupling: anything that built the orb differently, or
         * called the unwrapped method, silently stopped updating the badge.
         * The dashboard drives the orb through preview states the user is only
         * auditioning, so the badge stays out of it while it is open.
         *   [M14 P9.2] */
        onOrbStateChange((name) => {
            if (!orbDashActive) updateStatusBadge(name);
            emit(EVENTS.ORB_STATE, { state: name });
        });
    } catch (e) { console.warn('Orb init failed:', e); }
}

export function updateStatusBadge(stateName) {
    if (statusText) {
        statusText.textContent = ORB_STATE_LABELS[stateName] || 'Online';
    }
    if (statusDot) {
        // Remove all state-* classes
        statusDot.classList.remove(
            'state-listening', 'state-thinking', 'state-searching',
            'state-working', 'state-speaking'
        );
        if (stateName !== 'idle') {
            statusDot.classList.add('state-' + stateName);
        }
    }
}

/* ── updateOrbOcclusion ──  [M14 P2.3c]
 * The browser has no "is this element visually covered" API, so the UI that
 * covers the orb has to say so. Only surfaces that actually sit over the centre
 * of the screen count:
 *   - the settings panel, which is centred and opaque
 *   - the history rename/delete dialogs, whose backdrop covers everything
 * The side panels (activity, search results, history) and the orb dashboard are
 * deliberately excluded: the side panels leave the orb visible, and the whole
 * purpose of the dashboard is to preview orb changes live - pausing the orb
 * while the user drags its sliders would be the opposite of useful. */
export function updateOrbOcclusion() {
    if (!state.orb || typeof state.orb.setOccluded !== 'function') return;
    const covered =
        (settingsPanel && settingsPanel.classList.contains('open')) ||
        (historyDialogBackdrop && !historyDialogBackdrop.hidden);
    state.orb.setOccluded(!!covered);
}
