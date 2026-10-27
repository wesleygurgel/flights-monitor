// Campos de aeroporto do formulário de rotas: a pessoa digita a cidade ou o aeroporto e escolhe
// da lista. A sigla vai no campo oculto; "Chegando em" aceita vários (etiquetas removíveis).
(function () {
  document.querySelectorAll('[data-airport]').forEach(setup);

  function setup(root) {
    var many = root.dataset.airport === 'many';
    var hidden = root.querySelector('input[type=hidden]');
    var input = root.querySelector('[role=combobox]');
    var list = root.querySelector('[role=listbox]');
    var chips = root.querySelector('.chips');
    var opts = [], active = -1, timer = null, seq = 0;

    function codes() { return hidden.value ? hidden.value.split(',') : []; }

    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      active = -1;
    }

    function highlight(i) {
      active = i;
      Array.prototype.forEach.call(list.children, function (li, j) {
        li.setAttribute('aria-selected', j === i ? 'true' : 'false');
      });
      if (i >= 0) {
        input.setAttribute('aria-activedescendant', list.children[i].id);
        list.children[i].scrollIntoView({ block: 'nearest' });
      }
    }

    function render() {
      list.innerHTML = '';
      opts.forEach(function (o, i) {
        var li = document.createElement('li');
        li.id = list.id + '-' + i;
        li.setAttribute('role', 'option');
        li.setAttribute('aria-selected', 'false');
        var label = document.createElement('span');
        label.className = 'opt-label';
        label.textContent = o.label;
        var detail = document.createElement('span');
        detail.className = 'opt-detail';
        detail.textContent = o.detail;
        li.append(label, detail);
        // mousedown em vez de click: escolhe antes do blur fechar a lista
        li.addEventListener('mousedown', function (e) { e.preventDefault(); pick(i); });
        list.appendChild(li);
      });
      if (!opts.length) {
        var empty = document.createElement('li');
        empty.className = 'opt-empty';
        empty.textContent = 'Nenhum aeroporto encontrado. Tente o nome da cidade.';
        list.appendChild(empty);
      }
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      active = -1;
    }

    function query() {
      var q = input.value.trim();
      if (q.length < 2) { close(); return; }
      var mine = ++seq;
      fetch('/aeroportos?' + new URLSearchParams({ q: q, grupos: many ? '1' : '0' }))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || mine !== seq) return;  // resposta atrasada de uma digitação anterior
          opts = data;
          render();
        })
        .catch(function () {});
    }

    function addChip(item) {
      if (codes().indexOf(item.code) !== -1) return;
      hidden.value = codes().concat(item.code).join(',');
      var li = document.createElement('li');
      li.dataset.code = item.code;
      li.textContent = item.text;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.setAttribute('aria-label', 'Remover ' + item.text);
      btn.textContent = '×';
      li.appendChild(btn);
      chips.appendChild(li);
    }

    function removeChip(li) {
      hidden.value = codes().filter(function (c) { return c !== li.dataset.code; }).join(',');
      li.remove();
      input.focus();
    }

    function pick(i) {
      var o = opts[i];
      if (many) {
        o.items.forEach(addChip);
        input.value = '';
      } else {
        hidden.value = o.codes[0];
        input.value = o.items[0].text;
      }
      root.classList.remove('has-err');
      input.removeAttribute('aria-invalid');
      close();
    }

    input.addEventListener('input', function () {
      if (!many) hidden.value = '';  // texto mudou: só vale de novo quando escolher da lista
      clearTimeout(timer);
      timer = setTimeout(query, 150);
    });

    input.addEventListener('keydown', function (e) {
      var open = !list.hidden && opts.length;
      if (e.key === 'ArrowDown' && open) {
        e.preventDefault();
        highlight((active + 1) % opts.length);
      } else if (e.key === 'ArrowUp' && open) {
        e.preventDefault();
        highlight(active <= 0 ? opts.length - 1 : active - 1);
      } else if (e.key === 'Enter' && !list.hidden) {
        e.preventDefault();  // Enter escolhe da lista em vez de enviar o formulário
        if (opts.length) pick(active >= 0 ? active : 0);
      } else if (e.key === 'Escape' && !list.hidden) {
        e.preventDefault();
        close();
      } else if (e.key === 'Backspace' && many && !input.value && chips.lastElementChild) {
        removeChip(chips.lastElementChild);
      }
    });

    input.addEventListener('blur', function () { setTimeout(close, 100); });

    if (chips) {
      chips.addEventListener('click', function (e) {
        if (e.target.tagName === 'BUTTON') removeChip(e.target.parentElement);
      });
    }
  }
})();
