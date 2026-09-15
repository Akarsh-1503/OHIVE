'use client';

import { useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import type { SceneData } from '@/lib/recon';
import type { ColorMode } from './state';

/** Point radius in world units at `pointSize = 1`, expressed as a fraction of scene radius. */
const POINT_SIZE_BASE = 0.0042;

const VERT = /* glsl */ `
  attribute vec3 pointColor;
  uniform float uSize;
  uniform float uScale;
  uniform float uNear;
  uniform float uFar;
  varying vec3 vColor;
  varying float vFade;

  void main() {
    vColor = pointColor;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    float dist = max(-mv.z, 0.0001);
    // Capped: inside the cloud in follow-cam, uncapped attenuation turns nearby points into
    // screen-filling blobs.
    // Floor of 2.6 px, not 1.2: a corridor-shaped cloud spreads a few thousand points over a
    // large radius, and uSize scales with that radius, so distant points collapsed to a
    // single dim pixel and the whole scene read as empty.
    gl_PointSize = clamp(uSize * uScale / dist, 2.6, 16.0);
    // Depth cue: far points recede instead of fighting the near ones for attention.
    vFade = clamp(1.0 - (dist - uNear) / (uFar - uNear), 0.55, 1.0);
    gl_Position = projectionMatrix * mv;
  }
`;

const FRAG = /* glsl */ `
  uniform float uOpacity;
  varying vec3 vColor;
  varying float vFade;

  void main() {
    vec2 uv = gl_PointCoord - 0.5;
    float d2 = dot(uv, uv);
    if (d2 > 0.25) discard;
    float alpha = smoothstep(0.25, 0.045, d2) * uOpacity * vFade;
    if (alpha < 0.02) discard;
    // Slight core lift so clusters read as solid surface rather than speckle.
    vec3 c = vColor * (0.88 + 0.30 * (1.0 - min(d2 * 4.0, 1.0)));
    // No manual gamma here. pointColor is already sRGB (sampled straight from the source
    // frame), and the colorspace_fragment include below converts linear->sRGB for the
    // renderer output space. Applying pow(c, 2.2) as well darkened everything a second
    // time -- a mid-grey point landed near 0.22 -- which made sparse clouds read as an
    // empty canvas.
    gl_FragColor = vec4(c, alpha);
    #include <colorspace_fragment>
  }
`;

/**
 * The entire cloud is one `THREE.Points` over a single `BufferGeometry`. Colour modes swap a
 * pre-built `Uint8Array` attribute; the density slider moves the draw range over a shuffled
 * point order. Nothing here reallocates during interaction.
 */
export function PointCloud({
  data,
  colorMode,
  pointSize,
  density,
  visible,
}: {
  data: SceneData;
  colorMode: ColorMode;
  pointSize: number;
  density: number;
  visible: boolean;
}) {
  const { size, viewport, invalidate } = useThree();
  const pointsRef = useRef<THREE.Points>(null);

  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
    g.setAttribute('pointColor', new THREE.BufferAttribute(data.colorSource, 3, true));
    g.computeBoundingSphere();
    return g;
  }, [data]);

  const material = useMemo(() => {
    const r = data.bounds.radius;
    return new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      uniforms: {
        uSize: { value: POINT_SIZE_BASE * r },
        uScale: { value: 600 },
        uOpacity: { value: 1 },
        uNear: { value: r * 0.5 },
        uFar: { value: r * 4.5 },
      },
      transparent: true,
      depthWrite: true,
    });
  }, [data]);

  // Three.js owns GPU memory; React must hand it back explicitly.
  useEffect(() => {
    return () => {
      geometry.dispose();
      material.dispose();
    };
  }, [geometry, material]);

  useEffect(() => {
    const buffer =
      colorMode === 'rgb' ? data.colorSource : colorMode === 'height' ? data.colorHeight : data.colorObs;
    const attr = new THREE.BufferAttribute(buffer, 3, true);
    geometry.setAttribute('pointColor', attr);
    attr.needsUpdate = true;
    invalidate();
  }, [colorMode, data, geometry, invalidate]);

  useEffect(() => {
    geometry.setDrawRange(0, Math.max(1, Math.floor(data.pointCount * density)));
    invalidate();
  }, [density, data.pointCount, geometry, invalidate]);

  useEffect(() => {
    material.uniforms.uSize!.value = POINT_SIZE_BASE * data.bounds.radius * pointSize;
    invalidate();
  }, [pointSize, material, data.bounds.radius, invalidate]);

  useEffect(() => {
    // Matches three's own point-size convention so sprites stay the same screen size
    // regardless of canvas resolution or DPR.
    material.uniforms.uScale!.value = (size.height * viewport.dpr) / 2;
    invalidate();
  }, [size.height, viewport.dpr, material, invalidate]);

  return <points ref={pointsRef} geometry={geometry} material={material} frustumCulled={false} visible={visible} />;
}
