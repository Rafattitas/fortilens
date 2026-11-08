// Renders management/static/favicon.svg to the PNG fallbacks.
// Run in the Playwright image used for screenshots: node tools/render_icons.cjs
const fs=require('fs');const path=require('path');const {chromium}=require('playwright');
const root=path.join(__dirname,'..');const svg=fs.readFileSync(path.join(root,'management/static/favicon.svg'),'utf8');
// The home-screen icon is full-bleed: phones apply their own corner mask.
const square=svg.replace(' rx="7"','');
(async()=>{const browser=await chromium.launch();const page=await browser.newPage();
for(const [name,size,source] of [['favicon-32.png',32,svg],['apple-touch-icon.png',180,square]]){
  await page.setViewportSize({width:size,height:size});
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${source}`);
  await page.screenshot({path:path.join(root,'management/static',name),omitBackground:true});}
await browser.close();})();
