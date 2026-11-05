// The sidebar is off-canvas on small screens, so open it before following a link.
export async function nav(page, name) {
  const menu = page.getByRole('button', { name: 'Buka menu' })
  if (await menu.isVisible()) await menu.click()
  await page
    .locator('.sidebar')
    .getByRole('link', { name: new RegExp('^' + name) })
    .click()
}
