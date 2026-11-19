/*
 * Fills the startup screen from its URL query. Every visible string comes from
 * the fixed table below; the query only selects an entry, so a reason the
 * table does not know falls back to the generic error text.
 */
'use strict';

(function () {
  const TEXT = {
    en: {
      loadingTitle: 'Starting NANO…',
      loadingDetail: 'This can take up to a minute the first time.',
      loadingStatus: 'Loading',
      errorTitle: 'NANO could not open its interface',
      errorStatus: 'Stopped',
      code: 'Code',
      hint: 'Use "Restart Nano" in the tray menu, or close NANO and open it again. Your data is kept. The black console window shows the technical details.',
      reasons: {
        frontend_not_rendered: 'The interface files loaded but did not start. They are probably out of date: run NANO_DESKTOP.bat again and it rebuilds them.',
        frontend_load_failed: 'The interface did not load from NANO\'s engine.',
        renderer_gone: 'The window\'s display process stopped.',
        backend_start_failed: 'NANO\'s engine did not start.',
        backend_exited: 'NANO\'s engine stopped unexpectedly.',
      },
      generic: 'Something went wrong while opening NANO.',
    },
    pt: {
      loadingTitle: 'A iniciar o NANO…',
      loadingDetail: 'Da primeira vez pode demorar até um minuto.',
      loadingStatus: 'A carregar',
      errorTitle: 'O NANO não conseguiu abrir a interface',
      errorStatus: 'Parado',
      code: 'Código',
      hint: 'Usa "Reiniciar o Nano" no menu do tabuleiro, ou fecha o NANO e volta a abri-lo. Os teus dados são preservados. A janela preta da consola mostra os detalhes técnicos.',
      reasons: {
        frontend_not_rendered: 'Os ficheiros da interface carregaram mas não arrancaram. Provavelmente estão desatualizados: corre outra vez o NANO_DESKTOP.bat, que os volta a construir.',
        frontend_load_failed: 'A interface não carregou a partir do motor do NANO.',
        renderer_gone: 'O processo que desenha a janela parou.',
        backend_start_failed: 'O motor do NANO não arrancou.',
        backend_exited: 'O motor do NANO parou inesperadamente.',
      },
      generic: 'Algo correu mal ao abrir o NANO.',
    },
  };

  const lang = String(navigator.language || 'en').toLowerCase().startsWith('pt') ? 'pt' : 'en';
  const t = TEXT[lang];
  const params = new URLSearchParams(window.location.search);
  const state = params.get('state') === 'error' ? 'error' : 'loading';
  const reason = params.get('reason') || '';
  const set = (id, value) => { document.getElementById(id).textContent = value; };

  document.documentElement.lang = lang;
  document.body.dataset.state = state;
  if (state === 'loading') {
    set('title', t.loadingTitle);
    set('detail', t.loadingDetail);
    set('status', t.loadingStatus);
    return;
  }
  const known = Object.prototype.hasOwnProperty.call(t.reasons, reason);
  set('title', t.errorTitle);
  set('detail', known ? t.reasons[reason] : t.generic);
  set('status', t.errorStatus);
  set('hint-text', t.hint);
  // Only a known code is echoed back, so the page never displays arbitrary text.
  set('code', known ? t.code + ': ' + reason : '');
  document.getElementById('hint').hidden = false;
}());
