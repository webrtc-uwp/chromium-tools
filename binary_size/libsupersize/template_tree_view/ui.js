// Copyright 2018 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

'use strict';

/**
 * @fileoverview
 * UI classes and methods for the Binary Size Analysis HTML report.
 */

{
  /**
   * @enum {number} Various byte units and the corresponding amount of bytes
   * that one unit represents.
   */
  const _BYTE_UNITS = {
    GiB: 1024 ** 3,
    MiB: 1024 ** 2,
    KiB: 1024 ** 1,
    B: 1024 ** 0,
  };
  /** Set of all byte units */
  const _BYTE_UNITS_SET = new Set(Object.keys(_BYTE_UNITS));

  const _icons = document.getElementById('icons');
  /**
   * @enum {SVGSVGElement} Icon elements that correspond to each symbol type.
   */
  const _SYMBOL_ICONS = {
    D: _icons.querySelector('.foldericon'),
    F: _icons.querySelector('.fileicon'),
    b: _icons.querySelector('.bssicon'),
    d: _icons.querySelector('.dataicon'),
    r: _icons.querySelector('.readonlyicon'),
    t: _icons.querySelector('.codeicon'),
    v: _icons.querySelector('.vtableicon'),
    '*': _icons.querySelector('.generatedicon'),
    x: _icons.querySelector('.dexicon'),
    m: _icons.querySelector('.dexmethodicon'),
    p: _icons.querySelector('.localpakicon'),
    P: _icons.querySelector('.nonlocalpakicon'),
    o: _icons.querySelector('.othericon'), // used as default icon
  };

  // Templates for tree nodes in the UI.
  const _leafTemplate = document.getElementById('treeitem');
  const _treeTemplate = document.getElementById('treefolder');

  /**
   * @type {WeakMap<HTMLElement, Readonly<TreeNode>>}
   * Associates UI nodes with the corresponding tree data object
   * so that event listeners and other methods can
   * query the original data.
   */
  const _uiNodeData = new WeakMap();

  /**
   * Replace the contexts of the size element for a tree node.
   * The unit to use is selected from the current state,
   * and the original number of bytes will be displayed as
   * hover text over the element.
   * @param {HTMLElement} sizeElement Element that shoudl display the byte size
   * @param {number} bytes Number of bytes to use for the size text
   */
  function _setSizeContents(sizeElement, bytes) {
    // Get unit from query string, will fallback for invalid query
    const suffix = state.get('byteunit', {
      default: 'MiB',
      valid: _BYTE_UNITS_SET,
    });
    const value = _BYTE_UNITS[suffix];

    // Format the bytes as a number with 2 digits after the decimal point
    const text = (bytes / value).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    const textNode = document.createTextNode(`${text} `);

    // Display the suffix with a smaller font
    const suffixElement = document.createElement('small');
    suffixElement.textContent = suffix;

    // Replace the contents of '.size' and change its title
    dom.replace(sizeElement, dom.createFragment([textNode, suffixElement]));
    sizeElement.title =
      bytes.toLocaleString(undefined, {useGrouping: true}) + ' bytes';
  }

  /**
   * Click event handler to expand or close the child group of a tree.
   * @param {Event} event
   */
  function _toggleTreeElement(event) {
    event.preventDefault();

    const link = event.currentTarget;
    const element = link.parentElement;
    const group = link.nextElementSibling;

    const isExpanded = element.getAttribute('aria-expanded') === 'true';
    if (isExpanded) {
      element.setAttribute('aria-expanded', 'false');
      group.setAttribute('hidden', '');
    } else {
      if (group.children.length === 0) {
        const data = _uiNodeData.get(link);
        group.appendChild(
          dom.createFragment(data.children.map(child => newTreeElement(child)))
        );
      }

      element.setAttribute('aria-expanded', 'true');
      group.removeAttribute('hidden');
    }
  }

  /**
   * Inflate a template to create an element that represents one tree node.
   * The element will represent a tree or a leaf, depending on if the tree
   * node object has any children. Trees use a slightly different template
   * and have click event listeners attached.
   * @param {TreeNode} data Data to use for the UI.
   * @returns {HTMLElement}
   */
  function newTreeElement(data) {
    const isLeaf = data.children.length === 0;
    const template = isLeaf ? _leafTemplate : _treeTemplate;
    const element = document.importNode(template.content, true);

    // Associate clickable node & tree data
    const link = element.querySelector('.node');
    _uiNodeData.set(link, Object.freeze(data));

    // Icons are predefined in the HTML through hidden SVG elements
    const iconTemplate = _SYMBOL_ICONS[data.type] || _SYMBOL_ICONS.o;
    const icon = iconTemplate.cloneNode(true);
    // Insert an SVG icon at the start of the link to represent type
    link.insertBefore(icon, link.firstElementChild);

    // Set the symbol name and hover text
    const symbolName = element.querySelector('.symbol-name');
    symbolName.textContent = data.shortName;
    symbolName.title = data.idPath;

    // Set the byte size and hover text
    _setSizeContents(element.querySelector('.size'), data.size);

    if (!isLeaf) {
      link.addEventListener('click', _toggleTreeElement);
    }

    return element;
  }

  // When the `byteunit` state changes, update all .size elements in the page
  form.byteunit.addEventListener('change', event => {
    // Update existing size elements with the new unit
    for (const sizeElement of document.getElementsByClassName('size')) {
      const data = _uiNodeData.get(sizeElement.parentElement);
      _setSizeContents(sizeElement, data.size);
    }
  });
  function _toggleOptions() {
    document.body.classList.toggle('show-options');
  }
  for (const button of document.getElementsByClassName('toggle-options')) {
    button.addEventListener('click', _toggleOptions);
  }

  self.newTreeElement = newTreeElement;
}

{
  const blob = new Blob([
    `
    --INSERT_WORKER_CODE--
    `,
  ]);
  // We use a worker to keep large tree creation logic off the UI thread
  const worker = new Worker(URL.createObjectURL(blob));

  /**
   * Displays the given data as a tree view
   * @param {{data:TreeNode}} param0
   */
  worker.onmessage = ({data}) => {
    const root = newTreeElement(data);
    // Expand the root UI node
    root.querySelector('.node').click();

    dom.replace(document.getElementById('symboltree'), root);
  };

  /**
   * Loads the tree data given on a worker thread and replaces the tree view in
   * the UI once complete. Uses query string as state for the filter.
   * @param {string} treeData JSON string to be parsed on the worker thread.
   */
  function loadTree(treeData) {
    // Post as a JSON string for better performance
    worker.postMessage(
      `{"tree":${treeData}, "filters":"${location.search.slice(1)}"}`
    );
  }

  self.loadTree = loadTree;
}