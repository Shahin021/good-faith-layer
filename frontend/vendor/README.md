Put the bundled SDK here as `genlayer-js.js`.

```
npm i genlayer-js@2.0.0-rc.1 esbuild
npx esbuild node_modules/genlayer-js/dist/index.js \
  --bundle --format=esm --platform=browser \
  --outfile=frontend/vendor/genlayer-js.js
```

Until that file exists the page falls back to esm.sh. The bar above the
verifier shows which source was used.
