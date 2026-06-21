const x = `attribute vec4 a_position;
attribute vec4 a_color;
attribute vec2 a_texCoord;

uniform mat4 u_modelViewMatrix;
uniform mat4 u_projectionMatrix;

varying vec4 v_color;
varying vec2 v_texCoord;

void main() {
  gl_Position = u_projectionMatrix * u_modelViewMatrix * a_position;
  v_color = a_color;
  v_texCoord = a_texCoord;
} `, T = `precision mediump float;
uniform float u_lineWidth;
uniform float u_lineSpacing;

varying vec4 v_color;
varying vec2 v_texCoord;

void main() {
  vec2 uv = v_texCoord;
  
  // Create line patterns based on face color
  float pattern = 0.0;
  float threshold = 1.0 - u_lineWidth;
  
  // Different line patterns for different faces
  if (v_color.r > 0.9 && v_color.g < 0.1 && v_color.b < 0.1) {
    // Red face - horizontal lines
    pattern = step(threshold, mod(uv.y * u_lineSpacing, 1.0));
  } else if (v_color.r < 0.1 && v_color.g > 0.9 && v_color.b < 0.1) {
    // Green face - vertical lines  
    pattern = step(threshold, mod(uv.x * u_lineSpacing, 1.0));
  } else if (v_color.r < 0.1 && v_color.g < 0.1 && v_color.b > 0.9) {
    // Blue face - horizontal lines with slightly different spacing
    pattern = step(threshold, mod(uv.y * (u_lineSpacing * 0.9), 1.0));
  } else if (v_color.r > 0.9 && v_color.g > 0.9 && v_color.b < 0.1) {
    // Yellow face - vertical lines with slightly different spacing
    pattern = step(threshold, mod(uv.x * (u_lineSpacing * 0.9), 1.0));
  } else if (v_color.r > 0.9 && v_color.g < 0.1 && v_color.b > 0.9) {
    // Magenta face - diagonal lines
    pattern = step(threshold, mod((uv.x + uv.y) * (u_lineSpacing * 0.75), 1.0));
  } else {
    // Cyan face - horizontal lines with slightly different spacing
    pattern = step(threshold, mod(uv.y * (u_lineSpacing * 1.1), 1.0));
  }
  
  // Add some glow effect
  float glow = pattern * 0.8 + 0.2;
  
  gl_FragColor = vec4(v_color.rgb * glow, 1.0);
} `, y = `attribute vec4 a_position;
attribute vec2 a_texCoord;

varying vec2 v_texCoord;

void main() {
  gl_Position = a_position;
  v_texCoord = a_texCoord;
} `, z = `precision mediump float;
uniform sampler2D u_texture;
uniform vec2 u_resolution;
uniform float u_time;
uniform float u_strength;

varying vec2 v_texCoord;

void main() {
  vec2 uv = v_texCoord;
  vec2 center = vec2(0.5, 0.5);
  
  // Distance from center
  vec2 delta = uv - center;
  float distance = length(delta);
  
  // Fisheye distortion with animated strength
  float animatedStrength = u_strength * (1.0 + 0.2 * sin(u_time * 2.0));
  float distortion = 1.0 + animatedStrength * distance * distance;
  
  // Apply distortion
  vec2 distortedUV = center + delta / distortion;
  
  // Add chromatic aberration for extra effect
  float aberration = animatedStrength * 0.01;
  vec2 redOffset = distortedUV + delta * aberration;
  vec2 greenOffset = distortedUV;
  vec2 blueOffset = distortedUV - delta * aberration;
  
  // Sample color channels with slight offset
  float r = texture2D(u_texture, redOffset).r;
  float g = texture2D(u_texture, greenOffset).g;
  float b = texture2D(u_texture, blueOffset).b;
  
  // Vignette effect
  float vignette = 1.0 - distance * 0.7;
  vignette = smoothstep(0.0, 1.0, vignette);
  
  gl_FragColor = vec4(r, g, b, 1.0) * vignette;
} `;
class U {
  constructor(t) {
    if (this.canvas = t, this.gl = t.getContext("webgl") || t.getContext("experimental-webgl"), this.program = null, this.fisheyeProgram = null, this.framebuffer = null, this.colorTexture = null, this.depthBuffer = null, this.animationId = null, this.startTime = Date.now(), this.isRunning = !1, this.uniforms = {
      u_modelViewMatrix: null,
      u_projectionMatrix: null,
      u_lineWidth: null,
      u_lineSpacing: null
    }, this.fisheyeUniforms = {
      u_texture: null,
      u_resolution: null,
      u_time: null,
      u_strength: null
    }, this.attributeLocations = {
      a_position: null,
      a_color: null,
      a_texCoord: null
    }, this.fisheyeAttributeLocations = {
      a_position: null,
      a_texCoord: null
    }, this.buffers = {
      position: null,
      color: null,
      texCoord: null,
      indices: null,
      quadPosition: null,
      quadTexCoord: null
    }, this.currentRotation = { x: 0, y: 0, z: 0 }, this.rotationX = 3e-3, this.rotationY = 3e-3, this.rotationZ = 3e-3, this.config = {
      speed: 0.3,
      fisheyeStrength: 0.1,
      cameraDistance: 0.5,
      lineWidth: 0.2,
      lineSpacing: 80
    }, !this.gl)
      throw new Error("WebGL not supported");
    this.init();
  }
  async init() {
    await this.loadShaders(), this.setupFramebuffer(), this.setupCubeGeometry(), this.setupQuadGeometry(), this.setupUniforms(), this.setupEventListeners(), this.resizeCanvas();
  }
  async loadShaders() {
    try {
      this.program = this.createShaderProgram(x, T), this.fisheyeProgram = this.createShaderProgram(y, z);
    } catch (t) {
      throw console.error("Failed to load shaders:", t), t;
    }
  }
  createShaderProgram(t, r) {
    const e = this.createShader(this.gl.VERTEX_SHADER, t), i = this.createShader(this.gl.FRAGMENT_SHADER, r), s = this.gl.createProgram();
    return this.gl.attachShader(s, e), this.gl.attachShader(s, i), this.gl.linkProgram(s), this.gl.getProgramParameter(s, this.gl.LINK_STATUS) ? s : (console.error("Error linking shader program:", this.gl.getProgramInfoLog(s)), null);
  }
  createShader(t, r) {
    const e = this.gl.createShader(t);
    return this.gl.shaderSource(e, r), this.gl.compileShader(e), this.gl.getShaderParameter(e, this.gl.COMPILE_STATUS) ? e : (console.error("Error compiling shader:", this.gl.getShaderInfoLog(e)), this.gl.deleteShader(e), null);
  }
  updateConfig(t) {
    this.config = { ...this.config, ...t }, this.updateRotationSpeeds();
  }
  updateRotationSpeeds() {
    this.rotationX = (Math.random() * 0.01 + 3e-3) * this.config.speed, this.rotationY = (Math.random() * 0.01 + 3e-3) * this.config.speed, this.rotationZ = (Math.random() * 0.01 + 3e-3) * this.config.speed;
  }
  setupEventListeners() {
    window.addEventListener("resize", () => this.resizeCanvas());
  }
  resizeCanvas() {
    const t = this.canvas.getBoundingClientRect();
    this.canvas.width = t.width, this.canvas.height = t.height, this.gl.viewport(0, 0, this.canvas.width, this.canvas.height), this.colorTexture && this.depthBuffer && (this.gl.bindTexture(this.gl.TEXTURE_2D, this.colorTexture), this.gl.texImage2D(this.gl.TEXTURE_2D, 0, this.gl.RGBA, this.canvas.width, this.canvas.height, 0, this.gl.RGBA, this.gl.UNSIGNED_BYTE, null), this.gl.bindRenderbuffer(this.gl.RENDERBUFFER, this.depthBuffer), this.gl.renderbufferStorage(this.gl.RENDERBUFFER, this.gl.DEPTH_COMPONENT16, this.canvas.width, this.canvas.height));
  }
  setupFramebuffer() {
    this.gl.enable(this.gl.DEPTH_TEST), this.gl.depthFunc(this.gl.LEQUAL), this.gl.enable(this.gl.CULL_FACE), this.gl.cullFace(this.gl.BACK), this.framebuffer = this.gl.createFramebuffer(), this.gl.bindFramebuffer(this.gl.FRAMEBUFFER, this.framebuffer), this.colorTexture = this.gl.createTexture(), this.gl.bindTexture(this.gl.TEXTURE_2D, this.colorTexture), this.gl.texImage2D(this.gl.TEXTURE_2D, 0, this.gl.RGBA, this.canvas.width, this.canvas.height, 0, this.gl.RGBA, this.gl.UNSIGNED_BYTE, null), this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_MIN_FILTER, this.gl.LINEAR), this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_MAG_FILTER, this.gl.LINEAR), this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_WRAP_S, this.gl.CLAMP_TO_EDGE), this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_WRAP_T, this.gl.CLAMP_TO_EDGE), this.gl.framebufferTexture2D(this.gl.FRAMEBUFFER, this.gl.COLOR_ATTACHMENT0, this.gl.TEXTURE_2D, this.colorTexture, 0), this.depthBuffer = this.gl.createRenderbuffer(), this.gl.bindRenderbuffer(this.gl.RENDERBUFFER, this.depthBuffer), this.gl.renderbufferStorage(this.gl.RENDERBUFFER, this.gl.DEPTH_COMPONENT16, this.canvas.width, this.canvas.height), this.gl.framebufferRenderbuffer(this.gl.FRAMEBUFFER, this.gl.DEPTH_ATTACHMENT, this.gl.RENDERBUFFER, this.depthBuffer), this.gl.checkFramebufferStatus(this.gl.FRAMEBUFFER) !== this.gl.FRAMEBUFFER_COMPLETE && console.error("Framebuffer not complete"), this.gl.bindFramebuffer(this.gl.FRAMEBUFFER, null);
  }
  setupCubeGeometry() {
    const r = [
      // Front face (reversed winding for inner face)
      -10,
      -10,
      10,
      -10,
      10,
      10,
      10,
      10,
      10,
      10,
      -10,
      10,
      // Back face (reversed winding for inner face)  
      -10,
      -10,
      -10,
      10,
      -10,
      -10,
      10,
      10,
      -10,
      -10,
      10,
      -10,
      // Top face (reversed winding for inner face)
      -10,
      10,
      -10,
      10,
      10,
      -10,
      10,
      10,
      10,
      -10,
      10,
      10,
      // Bottom face (reversed winding for inner face)
      -10,
      -10,
      -10,
      -10,
      -10,
      10,
      10,
      -10,
      10,
      10,
      -10,
      -10,
      // Right face (reversed winding for inner face)
      10,
      -10,
      -10,
      10,
      -10,
      10,
      10,
      10,
      10,
      10,
      10,
      -10,
      // Left face (reversed winding for inner face)
      -10,
      -10,
      -10,
      -10,
      10,
      -10,
      -10,
      10,
      10,
      -10,
      -10,
      10
    ], e = [
      // Front face - Bright Cyan
      0,
      1,
      1,
      1,
      0,
      1,
      1,
      1,
      0,
      1,
      1,
      1,
      0,
      1,
      1,
      1,
      // Back face - Bright Magenta
      1,
      0,
      1,
      1,
      1,
      0,
      1,
      1,
      1,
      0,
      1,
      1,
      1,
      0,
      1,
      1,
      // Top face - Bright Yellow
      1,
      1,
      0,
      1,
      1,
      1,
      0,
      1,
      1,
      1,
      0,
      1,
      1,
      1,
      0,
      1,
      // Bottom face - Bright Green
      0,
      1,
      0,
      1,
      0,
      1,
      0,
      1,
      0,
      1,
      0,
      1,
      0,
      1,
      0,
      1,
      // Right face - Bright Red
      1,
      0,
      0,
      1,
      1,
      0,
      0,
      1,
      1,
      0,
      0,
      1,
      1,
      0,
      0,
      1,
      // Left face - Bright Blue
      0,
      0,
      1,
      1,
      0,
      0,
      1,
      1,
      0,
      0,
      1,
      1,
      0,
      0,
      1,
      1
    ], i = [
      0,
      1,
      2,
      0,
      2,
      3,
      // front
      4,
      5,
      6,
      4,
      6,
      7,
      // back
      8,
      9,
      10,
      8,
      10,
      11,
      // top
      12,
      13,
      14,
      12,
      14,
      15,
      // bottom
      16,
      17,
      18,
      16,
      18,
      19,
      // right
      20,
      21,
      22,
      20,
      22,
      23
      // left
    ];
    this.buffers.position = this.gl.createBuffer(), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.position), this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(r), this.gl.STATIC_DRAW), this.buffers.color = this.gl.createBuffer(), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.color), this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(e), this.gl.STATIC_DRAW);
    const s = [
      // Front face
      0,
      0,
      0,
      1,
      1,
      1,
      1,
      0,
      // Back face  
      0,
      0,
      1,
      0,
      1,
      1,
      0,
      1,
      // Top face
      0,
      0,
      1,
      0,
      1,
      1,
      0,
      1,
      // Bottom face
      0,
      0,
      0,
      1,
      1,
      1,
      1,
      0,
      // Right face
      0,
      0,
      0,
      1,
      1,
      1,
      1,
      0,
      // Left face
      0,
      0,
      1,
      0,
      1,
      1,
      0,
      1
    ];
    this.buffers.texCoord = this.gl.createBuffer(), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.texCoord), this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(s), this.gl.STATIC_DRAW), this.buffers.indices = this.gl.createBuffer(), this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER, this.buffers.indices), this.gl.bufferData(this.gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(i), this.gl.STATIC_DRAW);
  }
  setupQuadGeometry() {
    const t = [
      -1,
      -1,
      1,
      -1,
      1,
      1,
      -1,
      1
    ], r = [
      0,
      0,
      1,
      0,
      1,
      1,
      0,
      1
    ];
    this.buffers.quadPosition = this.gl.createBuffer(), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.quadPosition), this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(t), this.gl.STATIC_DRAW), this.buffers.quadTexCoord = this.gl.createBuffer(), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.quadTexCoord), this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(r), this.gl.STATIC_DRAW);
  }
  setupUniforms() {
    this.uniforms.u_modelViewMatrix = this.gl.getUniformLocation(this.program, "u_modelViewMatrix"), this.uniforms.u_projectionMatrix = this.gl.getUniformLocation(this.program, "u_projectionMatrix"), this.uniforms.u_lineWidth = this.gl.getUniformLocation(this.program, "u_lineWidth"), this.uniforms.u_lineSpacing = this.gl.getUniformLocation(this.program, "u_lineSpacing"), this.attributeLocations.a_position = this.gl.getAttribLocation(this.program, "a_position"), this.attributeLocations.a_color = this.gl.getAttribLocation(this.program, "a_color"), this.attributeLocations.a_texCoord = this.gl.getAttribLocation(this.program, "a_texCoord"), this.fisheyeUniforms.u_texture = this.gl.getUniformLocation(this.fisheyeProgram, "u_texture"), this.fisheyeUniforms.u_resolution = this.gl.getUniformLocation(this.fisheyeProgram, "u_resolution"), this.fisheyeUniforms.u_time = this.gl.getUniformLocation(this.fisheyeProgram, "u_time"), this.fisheyeUniforms.u_strength = this.gl.getUniformLocation(this.fisheyeProgram, "u_strength"), this.fisheyeAttributeLocations.a_position = this.gl.getAttribLocation(this.fisheyeProgram, "a_position"), this.fisheyeAttributeLocations.a_texCoord = this.gl.getAttribLocation(this.fisheyeProgram, "a_texCoord");
  }
  // Matrix math utilities
  createPerspectiveMatrix(t, r, e, i) {
    const s = Math.tan(Math.PI * 0.5 - 0.5 * t), n = 1 / (e - i);
    return [
      s / r,
      0,
      0,
      0,
      0,
      s,
      0,
      0,
      0,
      0,
      (e + i) * n,
      -1,
      0,
      0,
      e * i * n * 2,
      0
    ];
  }
  lookAt(t, r, e) {
    const [i, s, n] = t, [u, c, b] = r, [d, m, _] = e;
    let o = u - i, a = c - s, h = b - n, p = 1 / Math.hypot(o, a, h);
    o *= p, a *= p, h *= p;
    let l = a * _ - h * m, g = h * d - o * _, f = o * m - a * d, R = 1 / Math.hypot(l, g, f);
    l *= R, g *= R, f *= R;
    let E = g * h - f * a, A = f * o - l * h, v = l * a - g * o;
    return [
      l,
      E,
      -o,
      0,
      g,
      A,
      -a,
      0,
      f,
      v,
      -h,
      0,
      -(l * i + g * s + f * n),
      -(E * i + A * s + v * n),
      o * i + a * s + h * n,
      1
    ];
  }
  start() {
    this.isRunning || (this.isRunning = !0, this.render());
  }
  stop() {
    this.isRunning && (this.isRunning = !1, this.animationId && (cancelAnimationFrame(this.animationId), this.animationId = null));
  }
  render() {
    if (!this.isRunning) return;
    const t = (Date.now() - this.startTime) / 1e3;
    this.currentRotation.x += this.rotationX, this.currentRotation.y += this.rotationY, this.currentRotation.z += this.rotationZ;
    const r = this.config.fisheyeStrength > 0;
    this.gl.bindFramebuffer(this.gl.FRAMEBUFFER, r ? this.framebuffer : null), this.gl.viewport(0, 0, this.canvas.width, this.canvas.height), this.gl.useProgram(this.program), this.gl.clearColor(0, 0, 0, 1), this.gl.clearDepth(1), this.gl.clear(this.gl.COLOR_BUFFER_BIT | this.gl.DEPTH_BUFFER_BIT), this.gl.enable(this.gl.DEPTH_TEST), this.gl.enable(this.gl.CULL_FACE);
    const e = 45 * Math.PI / 180, i = this.canvas.width / this.canvas.height, s = this.createPerspectiveMatrix(e, i, 0.1, 100), n = this.config.cameraDistance, u = this.currentRotation.x, c = this.currentRotation.y, b = n * Math.sin(u) * Math.cos(c), d = n * Math.cos(u), m = n * Math.sin(u) * Math.sin(c), _ = this.lookAt([b, d, m], [0, 0, 0], [0, 1, 0]);
    this.gl.uniformMatrix4fv(this.uniforms.u_projectionMatrix, !1, s), this.gl.uniformMatrix4fv(this.uniforms.u_modelViewMatrix, !1, _), this.gl.uniform1f(this.uniforms.u_lineWidth, this.config.lineWidth), this.gl.uniform1f(this.uniforms.u_lineSpacing, this.config.lineSpacing), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.position), this.gl.vertexAttribPointer(this.attributeLocations.a_position, 3, this.gl.FLOAT, !1, 0, 0), this.gl.enableVertexAttribArray(this.attributeLocations.a_position), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.color), this.gl.vertexAttribPointer(this.attributeLocations.a_color, 4, this.gl.FLOAT, !1, 0, 0), this.gl.enableVertexAttribArray(this.attributeLocations.a_color), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.texCoord), this.gl.vertexAttribPointer(this.attributeLocations.a_texCoord, 2, this.gl.FLOAT, !1, 0, 0), this.gl.enableVertexAttribArray(this.attributeLocations.a_texCoord), this.gl.bindBuffer(this.gl.ELEMENT_ARRAY_BUFFER, this.buffers.indices), this.gl.drawElements(this.gl.TRIANGLES, 36, this.gl.UNSIGNED_SHORT, 0), r && (this.gl.bindFramebuffer(this.gl.FRAMEBUFFER, null), this.gl.viewport(0, 0, this.canvas.width, this.canvas.height), this.gl.useProgram(this.fisheyeProgram), this.gl.disable(this.gl.DEPTH_TEST), this.gl.disable(this.gl.CULL_FACE), this.gl.clearColor(0, 0, 0, 1), this.gl.clear(this.gl.COLOR_BUFFER_BIT), this.gl.activeTexture(this.gl.TEXTURE0), this.gl.bindTexture(this.gl.TEXTURE_2D, this.colorTexture), this.gl.uniform1i(this.fisheyeUniforms.u_texture, 0), this.gl.uniform2f(this.fisheyeUniforms.u_resolution, this.canvas.width, this.canvas.height), this.gl.uniform1f(this.fisheyeUniforms.u_time, t), this.gl.uniform1f(this.fisheyeUniforms.u_strength, this.config.fisheyeStrength), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.quadPosition), this.gl.vertexAttribPointer(this.fisheyeAttributeLocations.a_position, 2, this.gl.FLOAT, !1, 0, 0), this.gl.enableVertexAttribArray(this.fisheyeAttributeLocations.a_position), this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffers.quadTexCoord), this.gl.vertexAttribPointer(this.fisheyeAttributeLocations.a_texCoord, 2, this.gl.FLOAT, !1, 0, 0), this.gl.enableVertexAttribArray(this.fisheyeAttributeLocations.a_texCoord), this.gl.drawArrays(this.gl.TRIANGLE_FAN, 0, 4)), this.animationId = requestAnimationFrame(() => this.render());
  }
  destroy() {
    this.stop(), window.removeEventListener("resize", () => this.resizeCanvas()), this.program && this.gl.deleteProgram(this.program), this.fisheyeProgram && this.gl.deleteProgram(this.fisheyeProgram), this.framebuffer && this.gl.deleteFramebuffer(this.framebuffer), this.colorTexture && this.gl.deleteTexture(this.colorTexture), this.depthBuffer && this.gl.deleteRenderbuffer(this.depthBuffer);
  }
}
class C extends HTMLElement {
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.canvas = null, this.renderer = null;
  }
  static get observedAttributes() {
    return ["speed", "fisheye-strength", "camera-distance", "line-width", "line-spacing"];
  }
  attributeChangedCallback(t, r, e) {
    if (r !== e && this.renderer) {
      const i = {};
      switch (t) {
        case "speed":
          i.speed = parseFloat(e || "0.3");
          break;
        case "fisheye-strength":
          i.fisheyeStrength = parseFloat(e || "0.1");
          break;
        case "camera-distance":
          i.cameraDistance = parseFloat(e || "0.5");
          break;
        case "line-width":
          i.lineWidth = parseFloat(e || "0.2");
          break;
        case "line-spacing":
          i.lineSpacing = parseFloat(e || "80.0");
          break;
      }
      this.renderer.updateConfig(i);
    }
  }
  connectedCallback() {
    this.render(), this.setupRenderer();
  }
  disconnectedCallback() {
    this.renderer && (this.renderer.destroy(), this.renderer = null);
  }
  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          width: 100%;
          height: 100%;
          overflow: hidden;
        }
        canvas {
          width: 100%;
          height: 100%;
          display: block;
        }
      </style>
      <canvas id="max-headroom-canvas"></canvas>
    `, this.canvas = this.shadowRoot.getElementById("max-headroom-canvas");
  }
  async setupRenderer() {
    try {
      this.renderer = new U(this.canvas);
      const t = {
        speed: parseFloat(this.getAttribute("speed") || "0.3"),
        fisheyeStrength: parseFloat(this.getAttribute("fisheye-strength") || "0"),
        cameraDistance: parseFloat(this.getAttribute("camera-distance") || "0.5"),
        lineWidth: parseFloat(this.getAttribute("line-width") || "0.2"),
        lineSpacing: parseFloat(this.getAttribute("line-spacing") || "80.0")
      };
      this.renderer.updateConfig(t), this.renderer.start();
    } catch (t) {
      console.error("Failed to initialize Max Headroom renderer:", t);
    }
  }
}
customElements.define("max-headroom-bg", C);
export {
  C as default
};
