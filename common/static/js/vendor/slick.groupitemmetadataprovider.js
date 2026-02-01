"use strict";
(() => {
  var __defProp = Object.defineProperty;
  var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: !0, configurable: !0, writable: !0, value }) : obj[key] = value;
  var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key != "symbol" ? key + "" : key, value);

  // src/slick.groupitemmetadataprovider.ts
  var keyCode = Slick.keyCode, SlickGroup = Slick.Group, Utils = Slick.Utils, SlickGroupItemMetadataProvider = class {
    constructor(inputOptions) {
      __publicField(this, "pluginName", "GroupItemMetadataProvider");
      __publicField(this, "_grid");
      __publicField(this, "_options");
      __publicField(this, "_defaults", {
        checkboxSelect: !1,
        checkboxSelectCssClass: "slick-group-select-checkbox",
        checkboxSelectPlugin: null,
        groupCssClass: "slick-group",
        groupTitleCssClass: "slick-group-title",
        totalsCssClass: "slick-group-totals",
        groupFocusable: !0,
        totalsFocusable: !1,
        toggleCssClass: "slick-group-toggle",
        toggleExpandedCssClass: "expanded",
        toggleCollapsedCssClass: "collapsed",
        enableExpandCollapse: !0,
        groupFormatter: this.defaultGroupCellFormatter.bind(this),
        totalsFormatter: this.defaultTotalsCellFormatter.bind(this),
        includeHeaderTotals: !1
      });
      this._options = Utils.extend(!0, {}, this._defaults, inputOptions);
    }
    /** Getter of SlickGrid DataView object */
    get dataView() {
      var _a, _b, _c;
      return (_c = (_b = (_a = this._grid) == null ? void 0 : _a.getData) == null ? void 0 : _b.call(_a)) != null ? _c : {};
    }
    getOptions() {
      return this._options;
    }
    setOptions(inputOptions) {
      Utils.extend(!0, this._options, inputOptions);
    }
    defaultGroupCellFormatter(_row, _cell, _value, _columnDef, item) {
      var _a;
      if (!this._options.enableExpandCollapse)
        return item.title;
      let indentation = `${item.level * 15}px`, toggleClass = item.collapsed ? this._options.toggleCollapsedCssClass : this._options.toggleExpandedCssClass, containerElm = document.createDocumentFragment();
      this._options.checkboxSelect && containerElm.appendChild(Utils.createDomElement("span", { className: `${this._options.checkboxSelectCssClass} ${item.selectChecked ? "checked" : "unchecked"}` })), containerElm.appendChild(Utils.createDomElement("span", {
        className: `${this._options.toggleCssClass} ${toggleClass}`,
        ariaExpanded: String(!item.collapsed),
        style: { marginLeft: indentation }
      }));
      let groupTitleElm = Utils.createDomElement("span", { className: this._options.groupTitleCssClass || "" });
      return groupTitleElm.setAttribute("level", item.level), item.title instanceof HTMLElement ? groupTitleElm.appendChild(item.title) : this._grid.applyHtmlCode(groupTitleElm, (_a = item.title) != null ? _a : ""), containerElm.appendChild(groupTitleElm), containerElm;
    }
    defaultTotalsCellFormatter(_row, _cell, _value, columnDef, item, grid) {
      var _a, _b;
      return (_b = (_a = columnDef == null ? void 0 : columnDef.groupTotalsFormatter) == null ? void 0 : _a.call(columnDef, item, columnDef, grid)) != null ? _b : "";
    }
    init(grid) {
      this._grid = grid, this._grid.onClick.subscribe(this.handleGridClick.bind(this)), this._grid.onKeyDown.subscribe(this.handleGridKeyDown.bind(this));
    }
    destroy() {
      this._grid && (this._grid.onClick.unsubscribe(this.handleGridClick.bind(this)), this._grid.onKeyDown.unsubscribe(this.handleGridKeyDown.bind(this)));
    }
    handleGridClick(e, args) {
      let target = e.target, item = this._grid.getDataItem(args.row);
      if (item && item instanceof SlickGroup && target.classList.contains(this._options.toggleCssClass || "") && (this.handleDataViewExpandOrCollapse(item), e.stopImmediatePropagation(), e.preventDefault()), item && item instanceof SlickGroup && target.classList.contains(this._options.checkboxSelectCssClass || "")) {
        item.selectChecked = !item.selectChecked, target.classList.remove(item.selectChecked ? "unchecked" : "checked"), target.classList.add(item.selectChecked ? "checked" : "unchecked");
        let rowIndexes = this.dataView.mapItemsToRows(item.rows);
        item.selectChecked ? this._options.checkboxSelectPlugin.selectRows(rowIndexes) : this._options.checkboxSelectPlugin.deSelectRows(rowIndexes);
      }
    }
    // TODO:  add -/+ handling
    handleGridKeyDown(e) {
      if (this._options.enableExpandCollapse && e.which === keyCode.SPACE) {
        let activeCell = this._grid.getActiveCell();
        if (activeCell) {
          let item = this._grid.getDataItem(activeCell.row);
          item && item instanceof SlickGroup && (this.handleDataViewExpandOrCollapse(item), e.stopImmediatePropagation(), e.preventDefault());
        }
      }
    }
    handleDataViewExpandOrCollapse(item) {
      let range = this._grid.getRenderedRange();
      this.dataView.setRefreshHints({
        ignoreDiffsBefore: range.top,
        ignoreDiffsAfter: range.bottom + 1
      }), item.collapsed ? this.dataView.expandGroup(item.groupingKey) : this.dataView.collapseGroup(item.groupingKey);
    }
    getGroupRowMetadata(item, _row, _cell) {
      let groupLevel = item == null ? void 0 : item.level;
      return {
        selectable: !1,
        focusable: this._options.groupFocusable,
        cssClasses: `${this._options.groupCssClass} slick-group-level-${groupLevel}`,
        formatter: this._options.includeHeaderTotals && this._options.totalsFormatter || void 0,
        columns: {
          0: {
            colspan: this._options.includeHeaderTotals ? "1" : "*",
            formatter: this._options.groupFormatter,
            editor: null
          }
        }
      };
    }
    getTotalsRowMetadata(item, _row, _cell) {
      var _a;
      let groupLevel = (_a = item == null ? void 0 : item.group) == null ? void 0 : _a.level;
      return {
        selectable: !1,
        focusable: this._options.totalsFocusable,
        cssClasses: `${this._options.totalsCssClass} slick-group-level-${groupLevel}`,
        formatter: this._options.totalsFormatter,
        editor: null
      };
    }
  };
  window.Slick && (window.Slick.Data = window.Slick.Data || {}, window.Slick.Data.GroupItemMetadataProvider = SlickGroupItemMetadataProvider);
})();
//# sourceMappingURL=slick.groupitemmetadataprovider.js.map