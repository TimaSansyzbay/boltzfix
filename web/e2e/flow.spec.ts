import { test,expect } from '@playwright/test';
import { fileURLToPath } from 'node:url';

test('browser → API → chemistry/consensus → structure → frozen export',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'A better question for every pose.'})).toBeVisible();
  const create=page.waitForResponse(r=>r.url().endsWith('/v1/runs')&&r.request().method()==='POST');
  await page.getByRole('button',{name:/Run (another demo|synthetic example)/}).click();
  const response=await create;expect(response.status()).toBe(202);
  const run=await response.json();expect(run.synthetic).toBe(true);
  await expect(page.getByText('completed',{exact:true})).toBeVisible({timeout:45000});
  await expect(page.getByRole('heading',{name:'Selected candidate'})).toBeVisible();
  await expect(page.locator('.molecule canvas')).toBeVisible();
  await expect(page.locator('.viewer-message.error')).toHaveCount(0);
  await page.getByRole('button',{name:'Investigation',exact:true}).click();
  await expect(page.getByText('Chemistry proposes resample',{exact:true})).toBeVisible();
  await expect(page.getByText('Physics endorses a proposal',{exact:true}).first()).toBeVisible();
  await page.getByRole('button',{name:'Results & artifacts'}).click();
  await expect(page.getByText('Applicable physical checks passed',{exact:true})).toBeVisible();
  const download=page.waitForEvent('download');await page.getByRole('link',{name:'Run bundle'}).click();
  expect((await download).suggestedFilename()).toBe(`${run.id}.zip`);
  await page.getByRole('button',{name:'Overview',exact:true}).click();
  await page.screenshot({path:fileURLToPath(new URL('../../../outputs/boltzfix-preview.png',import.meta.url)),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('heading',{name:'A better question for every pose.'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('setup validates unsupported chemistry and keeps credentials out of UI',async({page})=>{
  await page.goto('/');await page.getByRole('button',{name:'New investigation',exact:true}).last().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByLabel(/Isomeric ligand SMILES/).fill('C');
  await page.getByRole('button',{name:'Start investigation'}).click();
  await expect(page.getByRole('alert')).toContainText('10–50 heavy atoms');
  await expect(page.locator('input[type=password]')).toHaveCount(0);
  await page.getByRole('button',{name:'Close setup'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
