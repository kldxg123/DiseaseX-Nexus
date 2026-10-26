const fs=require('fs');
const {chromium}=require('playwright');
(async()=>{
 const [username,password]=fs.readFileSync('F:\\作业\\aibola\\secrets\\dashboard_login.txt','utf8').replace(/^\uFEFF/,'').split(/\r?\n/);
 const browser=await chromium.launch({headless:true}); const context=await browser.newContext(); const page=await context.newPage();
 await page.goto('http://127.0.0.1:18130/login',{waitUntil:'domcontentloaded'}); await page.locator('#username').fill(username); await page.locator('#password').fill(password); await page.getByRole('button',{name:'登录'}).click(); await page.waitForLoadState('networkidle');
 const result=await page.evaluate(async()=>{
   const cat=await (await fetch('/api/v2/catalog/options',{credentials:'same-origin'})).json();
   const country=cat.countries.find(x=>x.country_iso2==='BR');
   const disease=cat.diseases.find(x=>String(x.disease_code).toLowerCase().includes('dengue')||String(x.disease_name_en).toLowerCase().includes('dengue'));
   if(!country||!disease) throw new Error('BRA dengue catalog identity unavailable');
   let cursor=null,items=[];
   do{const q=new URLSearchParams({country_iso2:'BR',disease_id:disease.disease_id,source_key:'all',page_size:'500'});if(cursor)q.set('cursor',cursor);const r=await fetch('/api/v2/history/series?'+q,{credentials:'same-origin'});if(!r.ok)throw new Error('series HTTP '+r.status);const x=await r.json();items.push(...x.items);cursor=x.next_cursor;}while(cursor);
   return {captured_at:new Date().toISOString(),country,disease,items};
 });
 const out='F:\\作业\\aibola\\reports\\nc_submission_revision_20260824\\NC_Submission_Updated_20260824\\Supplementary\\Python_Generated_Figures_20260824'; fs.mkdirSync(out,{recursive:true}); fs.writeFileSync(out+'\\Brazil_Dengue_History_Live.json',JSON.stringify(result,null,2),'utf8');
 console.log(JSON.stringify({rows:result.items.length,keys:result.items[0]?Object.keys(result.items[0]):[]})); await context.close();await browser.close();
})();
