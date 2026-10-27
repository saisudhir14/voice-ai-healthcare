const target = `http://127.0.0.1:${process.env.API_PORT || 7860}`;
module.exports = {
  '/api/**': {
    target,
    changeOrigin: true,
    timeout: 0,
    proxyTimeout: 0,
    headers: { 'Accept-Encoding': 'identity' },
  },
  '/ws': { target, ws: true, changeOrigin: true },
};
