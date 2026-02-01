"use strict";
(() => {
  var __defProp = Object.defineProperty;
  var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: !0, configurable: !0, writable: !0, value }) : obj[key] = value;
  var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key != "symbol" ? key + "" : key, value);

  // src/slick.editors.ts
  var keyCode = Slick.keyCode, Utils = Slick.Utils, TextEditor = class {
    constructor(args) {
      this.args = args;
      __publicField(this, "input");
      __publicField(this, "defaultValue");
      __publicField(this, "navOnLR");
      this.init();
    }
    init() {
      this.navOnLR = this.args.grid.getOptions().editorCellNavOnLRKeys, this.input = Utils.createDomElement("input", { type: "text", className: "editor-text" }, this.args.container), this.input.addEventListener("keydown", this.navOnLR ? handleKeydownLRNav : handleKeydownLRNoNav), this.input.focus(), this.input.select(), this.args.compositeEditorOptions && this.input.addEventListener("change", this.onChange.bind(this));
    }
    onChange() {
      var _a, _b;
      let activeCell = this.args.grid.getActiveCell();
      this.validate().valid && this.applyValue(this.args.item, this.serializeValue()), this.applyValue(this.args.compositeEditorOptions.formValues, this.serializeValue()), this.args.grid.onCompositeEditorChange.notify({
        row: (_a = activeCell == null ? void 0 : activeCell.row) != null ? _a : 0,
        cell: (_b = activeCell == null ? void 0 : activeCell.cell) != null ? _b : 0,
        item: this.args.item,
        column: this.args.column,
        formValues: this.args.compositeEditorOptions.formValues,
        grid: this.args.grid,
        editors: this.args.compositeEditorOptions.editors
      });
    }
    destroy() {
      this.input.removeEventListener("keydown", this.navOnLR ? handleKeydownLRNav : handleKeydownLRNoNav), this.input.removeEventListener("change", this.onChange.bind(this)), this.input.remove();
    }
    focus() {
      this.input.focus();
    }
    getValue() {
      return this.input.value;
    }
    setValue(val) {
      this.input.value = val;
    }
    loadValue(item) {
      var _a, _b;
      this.defaultValue = item[this.args.column.field] || "", this.input.value = String((_a = this.defaultValue) != null ? _a : ""), this.input.defaultValue = String((_b = this.defaultValue) != null ? _b : ""), this.input.select();
    }
    serializeValue() {
      return this.input.value;
    }
    applyValue(item, state) {
      item[this.args.column.field] = state;
    }
    isValueChanged() {
      return !(this.input.value === "" && !Utils.isDefined(this.defaultValue)) && this.input.value !== this.defaultValue;
    }
    validate() {
      if (this.args.column.validator) {
        let validationResults = this.args.column.validator(this.input.value, this.args);
        if (!validationResults.valid)
          return validationResults;
      }
      return {
        valid: !0,
        msg: null
      };
    }
  }, IntegerEditor = class {
    constructor(args) {
      this.args = args;
      __publicField(this, "input");
      __publicField(this, "defaultValue");
      __publicField(this, "navOnLR");
      this.init();
    }
    init() {
      this.navOnLR = this.args.grid.getOptions().editorCellNavOnLRKeys, this.input = Utils.createDomElement("input", { type: "text", className: "editor-text" }, this.args.container), this.input.addEventListener("keydown", this.navOnLR ? handleKeydownLRNav : handleKeydownLRNoNav), this.input.focus(), this.input.select(), this.args.compositeEditorOptions && this.input.addEventListener("change", this.onChange.bind(this));
    }
    onChange() {
      var _a, _b;
      let activeCell = this.args.grid.getActiveCell();
      this.validate().valid && this.applyValue(this.args.item, this.serializeValue()), this.applyValue(this.args.compositeEditorOptions.formValues, this.serializeValue()), this.args.grid.onCompositeEditorChange.notify({
        row: (_a = activeCell == null ? void 0 : activeCell.row) != null ? _a : 0,
        cell: (_b = activeCell == null ? void 0 : activeCell.cell) != null ? _b : 0,
        item: this.args.item,
        column: this.args.column,
        formValues: this.args.compositeEditorOptions.formValues,
        grid: this.args.grid,
        editors: this.args.compositeEditorOptions.editors
      });
    }
    destroy() {
      this.input.removeEventListener("keydown", this.navOnLR ? handleKeydownLRNav : handleKeydownLRNoNav), this.input.removeEventListener("change", this.onChange.bind(this)), this.input.remove();
    }
    focus() {
      this.input.focus();
    }
    loadValue(item) {
      var _a, _b;
      this.defaultValue = item[this.args.column.field], this.input.value = String((_a = this.defaultValue) != null ? _a : ""), this.input.defaultValue = String((_b = this.defaultValue) != null ? _b : ""), this.input.select();
    }
    serializeValue() {
      return parseInt(this.input.value, 10) || 0;
    }
    applyValue(item, state) {
      item[this.args.column.field] = state;
    }
    isValueChanged() {
      return !(this.input.value === "" && !Utils.isDefined(this.defaultValue)) && this.input.value !== this.defaultValue;
    }
    validate() {
      if (isNaN(this.input.value))
        return {
          valid: !1,
          msg: "Please enter a valid integer"
        };
      if (this.args.column.validator) {
        let validationResults = this.args.column.validator(this.input.value, this.args);
        if (!validationResults.valid)
          return validationResults;
      }
      return {
        valid: !0,
        msg: null
      };
    }
  }, _FloatEditor = class _FloatEditor {
    constructor(args) {
      this.args = args;
      __publicField(this, "input");
      __publicField(this, "defaultValue");
      __publicField(this, "navOnLR");
      this.init();
    }
    init() {
      this.navOnLR = this.args.grid.getOptions().editorCellNavOnLRKeys, this.input = Utils.createDomElement("input", { type: "text", className: "editor-text" }, this.args.container), this.input.addEventListener("keydown", this.navOnLR ? handleKeydownLRNav : handleKeydownLRNoNav), this.input.focus(), this.input.select(), this.args.compositeEditorOptions && this.input.addEventListener("change", this.onChange.bind(this));
    }
    onChange() {
      var _a, _b;
      let activeCell = this.args.grid.getActiveCell();
      this.validate().valid && this.applyValue(this.args.item, this.serializeValue()), this.applyValue(this.args.compositeEditorOptions.formValues, this.serializeValue()), this.args.grid.onCompositeEditorChange.notify({
        row: (_a = activeCell == null ? void 0 : activeCell.row) != null ? _a : 0,
        cell: (_b = activeCell == null ? void 0 : activeCell.cell) != null ? _b : 0,
        item: this.args.item,
        column: this.args.column,
        formValues: this.args.compositeEditorOptions.formValues,
        grid: this.args.grid,
        editors: this.args.compositeEditorOptions.editors
      });
    }
    destroy() {
      this.input.removeEventListener("keydown", this.navOnLR ? handleKeydownLRNav : handleKeydownLRNoNav), this.input.removeEventListener("change", this.onChange.bind(this)), this.input.remove();
    }
    focus() {
      this.input.focus();
    }
    getDecimalPlaces() {
      let rtn = this.args.column.editorFixedDecimalPlaces;
      return Utils.isDefined(rtn) || (rtn = _FloatEditor.DefaultDecimalPlaces), !rtn && rtn !== 0 ? null : rtn;
    }
    loadValue(item) {
      var _a, _b, _c;
      this.defaultValue = item[this.args.column.field];
      let decPlaces = this.getDecimalPlaces();
      decPlaces !== null && (this.defaultValue || this.defaultValue === 0) && ((_a = this.defaultValue) != null && _a.toFixed) && (this.defaultValue = this.defaultValue.toFixed(decPlaces)), this.input.value = String((_b = this.defaultValue) != null ? _b : ""), this.input.defaultValue = String((_c = this.defaultValue) != null ? _c : ""), this.input.select();
    }
    serializeValue() {
      let rtn = parseFloat(this.input.value);
      _FloatEditor.AllowEmptyValue ? !rtn && rtn !== 0 && (rtn = void 0) : rtn = rtn || 0;
      let decPlaces = this.getDecimalPlaces();
      return decPlaces !== null && (rtn || rtn === 0) && rtn.toFixed && (rtn = parseFloat(rtn.toFixed(decPlaces))), rtn;
    }
    applyValue(item, state) {
      item[this.args.column.field] = state;
    }
    isValueChanged() {
      return !(this.input.value === "" && !Utils.isDefined(this.defaultValue)) && this.input.value !== this.defaultValue;
    }
    validate() {
      if (isNaN(this.input.value))
        return {
          valid: !1,
          msg: "Please enter a valid number"
        };
      if (this.args.column.validator) {
        let validationResults = this.args.column.validator(this.input.value, this.args);
        if (!validationResults.valid)
          return validationResults;
      }
      return {
        valid: !0,
        msg: null
      };
    }
  };
  /** Default number of decimal places to use with FloatEditor */
  __publicField(_FloatEditor, "DefaultDecimalPlaces"), /** Should we allow empty value when using FloatEditor */
  __publicField(_FloatEditor, "AllowEmptyValue", !1);
  var FloatEditor = _FloatEditor, FlatpickrEditor = class {
    constructor(args) {
      this.args = args;
      __publicField(this, "input");
      __publicField(this, "defaultValue");
      __publicField(this, "flatpickrInstance");
      if (this.init(), typeof flatpickr == "undefined")
        throw new Error("Flatpickr not loaded but required in SlickGrid.Editors, refer to Flatpickr documentation: https://flatpickr.js.org/getting-started/");
    }
    init() {
      var _a, _b, _c;
      this.input = Utils.createDomElement("input", { type: "text", className: "editor-text" }, this.args.container), this.input.focus(), this.input.select();
      let editorOptions = (_a = this.args.column.params) == null ? void 0 : _a.editorOptions;
      this.flatpickrInstance = flatpickr(this.input, {
        closeOnSelect: !0,
        allowInput: !0,
        altInput: !0,
        altFormat: (_b = editorOptions == null ? void 0 : editorOptions.altFormat) != null ? _b : "m/d/Y",
        dateFormat: (_c = editorOptions == null ? void 0 : editorOptions.dateFormat) != null ? _c : "m/d/Y",
        onChange: () => {
          var _a2, _b2;
          if (this.args.compositeEditorOptions) {
            let activeCell = this.args.grid.getActiveCell();
            this.validate().valid && this.applyValue(this.args.item, this.serializeValue()), this.applyValue(this.args.compositeEditorOptions.formValues, this.serializeValue()), this.args.grid.onCompositeEditorChange.notify({
              row: (_a2 = activeCell == null ? void 0 : activeCell.row) != null ? _a2 : 0,
              cell: (_b2 = activeCell == null ? void 0 : activeCell.cell) != null ? _b2 : 0,
              item: this.args.item,
              column: this.args.column,
              formValues: this.args.compositeEditorOptions.formValues,
              grid: this.args.grid,
              editors: this.args.compositeEditorOptions.editors
            });
          }
        }
      }), this.args.compositeEditorOptions || window.setTimeout(() => {
        this.show(), this.focus();
      }, 50), Utils.width(this.input, Utils.width(this.input) - (this.args.compositeEditorOptions ? 28 : 18));
    }
    destroy() {
      this.hide(), this.flatpickrInstance && this.flatpickrInstance.destroy(), this.input.remove();
    }
    show() {
      !this.args.compositeEditorOptions && this.flatpickrInstance && this.flatpickrInstance.open();
    }
    hide() {
      !this.args.compositeEditorOptions && this.flatpickrInstance && this.flatpickrInstance.close();
    }
    focus() {
      this.input.focus();
    }
    loadValue(item) {
      var _a, _b;
      this.defaultValue = item[this.args.column.field], this.input.value = String((_a = this.defaultValue) != null ? _a : ""), this.input.defaultValue = String((_b = this.defaultValue) != null ? _b : ""), this.input.select(), this.flatpickrInstance && this.flatpickrInstance.setDate(this.defaultValue);
    }
    serializeValue() {
      return this.input.value;
    }
    applyValue(item, state) {
      item[this.args.column.field] = state;
    }
    isValueChanged() {
      return !(this.input.value === "" && !Utils.isDefined(this.defaultValue)) && this.input.value !== this.defaultValue;
    }
    validate() {
      if (this.args.column.validator) {
        let validationResults = this.args.column.validator(this.input.value, this.args);
        if (!validationResults.valid)
          return validationResults;
      }
      return {
        valid: !0,
        msg: null
      };
    }
  }, YesNoSelectEditor = class {
    constructor(args) {
      this.args = args;
      __publicField(this, "select");
      __publicField(this, "defaultValue");
      this.init();
    }
    init() {
      this.select = Utils.createDomElement("select", { tabIndex: 0, className: "editor-yesno" }, this.args.container), Utils.createDomElement("option", { value: "yes", textContent: "Yes" }, this.select), Utils.createDomElement("option", { value: "no", textContent: "No" }, this.select), this.select.focus(), this.args.compositeEditorOptions && this.select.addEventListener("change", this.onChange.bind(this));
    }
    onChange() {
      var _a, _b;
      let activeCell = this.args.grid.getActiveCell();
      this.validate().valid && this.applyValue(this.args.item, this.serializeValue()), this.applyValue(this.args.compositeEditorOptions.formValues, this.serializeValue()), this.args.grid.onCompositeEditorChange.notify({
        row: (_a = activeCell == null ? void 0 : activeCell.row) != null ? _a : 0,
        cell: (_b = activeCell == null ? void 0 : activeCell.cell) != null ? _b : 0,
        item: this.args.item,
        column: this.args.column,
        formValues: this.args.compositeEditorOptions.formValues,
        grid: this.args.grid,
        editors: this.args.compositeEditorOptions.editors
      });
    }
    destroy() {
      this.select.removeEventListener("change", this.onChange.bind(this)), this.select.remove();
    }
    focus() {
      this.select.focus();
    }
    loadValue(item) {
      this.select.value = (this.defaultValue = item[this.args.column.field]) ? "yes" : "no";
    }
    serializeValue() {
      return this.select.value === "yes";
    }
    applyValue(item, state) {
      item[this.args.column.field] = state;
    }
    isValueChanged() {
      return this.select.value !== this.defaultValue;
    }
    validate() {
      return {
        valid: !0,
        msg: null
      };
    }
  }, CheckboxEditor = class {
    constructor(args) {
      this.args = args;
      __publicField(this, "input");
      __publicField(this, "defaultValue");
      this.init();
    }
    init() {
      this.input = Utils.createDomElement("input", { className: "editor-checkbox", type: "checkbox", value: "true" }, this.args.container), this.input.focus(), this.args.compositeEditorOptions && this.input.addEventListener("change", this.onChange.bind(this));
    }
    onChange() {
      var _a, _b;
      let activeCell = this.args.grid.getActiveCell();
      this.validate().valid && this.applyValue(this.args.item, this.serializeValue()), this.applyValue(this.args.compositeEditorOptions.formValues, this.serializeValue()), this.args.grid.onCompositeEditorChange.notify({
        row: (_a = activeCell == null ? void 0 : activeCell.row) != null ? _a : 0,
        cell: (_b = activeCell == null ? void 0 : activeCell.cell) != null ? _b : 0,
        item: this.args.item,
        column: this.args.column,
        formValues: this.args.compositeEditorOptions.formValues,
        grid: this.args.grid,
        editors: this.args.compositeEditorOptions.editors
      });
    }
    destroy() {
      this.input.removeEventListener("change", this.onChange.bind(this)), this.input.remove();
    }
    focus() {
      this.input.focus();
    }
    loadValue(item) {
      this.defaultValue = !!item[this.args.column.field], this.defaultValue ? this.input.checked = !0 : this.input.checked = !1;
    }
    serializeValue() {
      return this.input.checked;
    }
    applyValue(item, state) {
      item[this.args.column.field] = state;
    }
    isValueChanged() {
      return this.serializeValue() !== this.defaultValue;
    }
    validate() {
      return {
        valid: !0,
        msg: null
      };
    }
  }, PercentCompleteEditor = class {
    constructor(args) {
      this.args = args;
      __publicField(this, "input");
      __publicField(this, "defaultValue");
      __publicField(this, "picker");
      __publicField(this, "slider");
      this.init();
    }
    sliderInputHandler(e) {
      this.input.value = e.target.value;
    }
    sliderChangeHandler() {
      var _a, _b;
      if (this.args.compositeEditorOptions) {
        let activeCell = this.args.grid.getActiveCell();
        this.validate().valid && this.applyValue(this.args.item, this.serializeValue()), this.applyValue(this.args.compositeEditorOptions.formValues, this.serializeValue()), this.args.grid.onCompositeEditorChange.notify({
          row: (_a = activeCell == null ? void 0 : activeCell.row) != null ? _a : 0,
          cell: (_b = activeCell == null ? void 0 : activeCell.cell) != null ? _b : 0,
          item: this.args.item,
          column: this.args.column,
          formValues: this.args.compositeEditorOptions.formValues,
          grid: this.args.grid,
          editors: this.args.compositeEditorOptions.editors
        });
      }
    }
    init() {
      var _a;
      this.input = Utils.createDomElement("input", { className: "editor-percentcomplete", type: "text" }, this.args.container), Utils.width(this.input, this.args.container.clientWidth - 25), this.picker = Utils.createDomElement("div", { className: "editor-percentcomplete-picker" }, this.args.container), Utils.createDomElement("span", { className: "editor-percentcomplete-picker-icon" }, this.picker);
      let containerHelper = Utils.createDomElement("div", { className: "editor-percentcomplete-helper" }, this.picker), containerWrapper = Utils.createDomElement("div", { className: "editor-percentcomplete-wrapper" }, containerHelper);
      Utils.createDomElement("div", { className: "editor-percentcomplete-slider" }, containerWrapper), this.slider = Utils.createDomElement("input", { className: "editor-percentcomplete-slider", type: "range", value: String((_a = this.defaultValue) != null ? _a : "") }, containerWrapper);
      let containerButtons = Utils.createDomElement("div", { className: "editor-percentcomplete-buttons" }, containerWrapper);
      Utils.createDomElement("button", { value: "0", className: "slick-btn slick-btn-default", textContent: "Not started" }, containerButtons), containerButtons.appendChild(document.createElement("br")), Utils.createDomElement("button", { value: "50", className: "slick-btn slick-btn-default", textContent: "In Progress" }, containerButtons), containerButtons.appendChild(document.createElement("br")), Utils.createDomElement("button", { value: "100", className: "slick-btn slick-btn-default", textContent: "Complete" }, containerButtons), this.input.focus(), this.input.select(), this.slider.addEventListener("input", this.sliderInputHandler.bind(this)), this.slider.addEventListener("change", this.sliderChangeHandler.bind(this));
      let buttons = this.picker.querySelectorAll(".editor-percentcomplete-buttons button");
      [].forEach.call(buttons, (button) => {
        button.addEventListener("click", this.onClick.bind(this));
      });
    }
    onClick(e) {
      var _a, _b;
      this.input.value = String((_a = e.target.value) != null ? _a : ""), this.slider.value = String((_b = e.target.value) != null ? _b : "");
    }
    destroy() {
      var _a, _b;
      (_a = this.slider) == null || _a.removeEventListener("input", this.sliderInputHandler.bind(this)), (_b = this.slider) == null || _b.removeEventListener("change", this.sliderChangeHandler.bind(this)), this.picker.querySelectorAll(".editor-percentcomplete-buttons button").forEach((button) => button.removeEventListener("click", this.onClick.bind(this))), this.input.remove(), this.picker.remove();
    }
    focus() {
      this.input.focus();
    }
    loadValue(item) {
      var _a;
      this.defaultValue = item[this.args.column.field], this.slider.value = String((_a = this.defaultValue) != null ? _a : ""), this.input.value = String(this.defaultValue), this.input.select();
    }
    serializeValue() {
      return parseInt(this.input.value, 10) || 0;
    }
    applyValue(item, state) {
      item[this.args.column.field] = state;
    }
    isValueChanged() {
      return !(this.input.value === "" && !Utils.isDefined(this.defaultValue)) && (parseInt(this.input.value, 10) || 0) !== this.defaultValue;
    }
    validate() {
      return isNaN(parseInt(this.input.value, 10)) ? {
        valid: !1,
        msg: "Please enter a valid positive number"
      } : {
        valid: !0,
        msg: null
      };
    }
  }, LongTextEditor = class {
    constructor(args) {
      this.args = args;
      __publicField(this, "input");
      __publicField(this, "wrapper");
      __publicField(this, "defaultValue");
      __publicField(this, "selectionStart", 0);
      this.init();
    }
    init() {
      let compositeEditorOptions = this.args.compositeEditorOptions;
      this.args.grid.getOptions().editorCellNavOnLRKeys;
      let container = compositeEditorOptions ? this.args.container : document.body;
      if (this.wrapper = Utils.createDomElement("div", { className: "slick-large-editor-text" }, container), compositeEditorOptions ? (this.wrapper.style.position = "relative", Utils.setStyleSize(this.wrapper, "padding", 0), Utils.setStyleSize(this.wrapper, "border", 0)) : this.wrapper.style.position = "absolute", this.input = Utils.createDomElement("textarea", { rows: 5, style: { background: "white", width: "250px", height: "80px", border: "0", outline: "0" } }, this.wrapper), compositeEditorOptions)
        this.input.addEventListener("change", this.onChange.bind(this));
      else {
        let btnContainer = Utils.createDomElement("div", { style: "text-align:right" }, this.wrapper);
        Utils.createDomElement("button", { id: "save", className: "slick-btn slick-btn-primary", textContent: "Save" }, btnContainer), Utils.createDomElement("button", { id: "cancel", className: "slick-btn slick-btn-default", textContent: "Cancel" }, btnContainer), this.wrapper.querySelector("#save").addEventListener("click", this.save.bind(this)), this.wrapper.querySelector("#cancel").addEventListener("click", this.cancel.bind(this)), this.input.addEventListener("keydown", this.handleKeyDown.bind(this)), this.position(this.args.position);
      }
      this.input.focus(), this.input.select();
    }
    onChange() {
      var _a, _b;
      let activeCell = this.args.grid.getActiveCell();
      this.validate().valid && this.applyValue(this.args.item, this.serializeValue()), this.applyValue(this.args.compositeEditorOptions.formValues, this.serializeValue()), this.args.grid.onCompositeEditorChange.notify({
        row: (_a = activeCell == null ? void 0 : activeCell.row) != null ? _a : 0,
        cell: (_b = activeCell == null ? void 0 : activeCell.cell) != null ? _b : 0,
        item: this.args.item,
        column: this.args.column,
        formValues: this.args.compositeEditorOptions.formValues,
        grid: this.args.grid,
        editors: this.args.compositeEditorOptions.editors
      });
    }
    handleKeyDown(e) {
      if (e.which === keyCode.ENTER && e.ctrlKey)
        this.save();
      else if (e.which === keyCode.ESCAPE)
        e.preventDefault(), this.cancel();
      else if (e.which === keyCode.TAB && e.shiftKey)
        e.preventDefault(), this.args.grid.navigatePrev();
      else if (e.which === keyCode.TAB)
        e.preventDefault(), this.args.grid.navigateNext();
      else if ((e.which === keyCode.LEFT || e.which === keyCode.RIGHT) && this.args.grid.getOptions().editorCellNavOnLRKeys) {
        let cursorPosition = this.selectionStart, textLength = e.target.value.length;
        e.keyCode === keyCode.LEFT && cursorPosition === 0 && this.args.grid.navigatePrev(), e.keyCode === keyCode.RIGHT && cursorPosition >= textLength - 1 && this.args.grid.navigateNext();
      }
    }
    save() {
      (this.args.grid.getOptions() || {}).autoCommitEdit ? this.args.grid.getEditorLock().commitCurrentEdit() : this.args.commitChanges();
    }
    cancel() {
      var _a;
      this.input.value = String((_a = this.defaultValue) != null ? _a : ""), this.args.cancelChanges();
    }
    hide() {
      Utils.hide(this.wrapper);
    }
    show() {
      Utils.show(this.wrapper);
    }
    position(position) {
      Utils.setStyleSize(this.wrapper, "top", (position.top || 0) - 5), Utils.setStyleSize(this.wrapper, "left", (position.left || 0) - 2);
    }
    destroy() {
      this.args.compositeEditorOptions ? this.input.removeEventListener("change", this.onChange.bind(this)) : (this.wrapper.querySelector("#save").removeEventListener("click", this.save.bind(this)), this.wrapper.querySelector("#cancel").removeEventListener("click", this.cancel.bind(this)), this.input.removeEventListener("keydown", this.handleKeyDown.bind(this))), this.wrapper.remove();
    }
    focus() {
      this.input.focus();
    }
    loadValue(item) {
      this.input.value = this.defaultValue = item[this.args.column.field], this.input.select();
    }
    serializeValue() {
      return this.input.value;
    }
    applyValue(item, state) {
      item[this.args.column.field] = state;
    }
    isValueChanged() {
      return !(this.input.value === "" && !Utils.isDefined(this.defaultValue)) && this.input.value !== this.defaultValue;
    }
    validate() {
      if (this.args.column.validator) {
        let validationResults = this.args.column.validator(this.input.value, this.args);
        if (!validationResults.valid)
          return validationResults;
      }
      return {
        valid: !0,
        msg: null
      };
    }
  };
  function handleKeydownLRNav(e) {
    let cursorPosition = e.selectionStart, textLength = e.target.value.length;
    (e.keyCode === keyCode.LEFT && cursorPosition > 0 || e.keyCode === keyCode.RIGHT && cursorPosition < textLength - 1) && e.stopImmediatePropagation();
  }
  function handleKeydownLRNoNav(e) {
    (e.keyCode === keyCode.LEFT || e.keyCode === keyCode.RIGHT) && e.stopImmediatePropagation();
  }
  var Editors = {
    Text: TextEditor,
    Integer: IntegerEditor,
    Float: FloatEditor,
    Flatpickr: FlatpickrEditor,
    YesNoSelect: YesNoSelectEditor,
    Checkbox: CheckboxEditor,
    PercentComplete: PercentCompleteEditor,
    LongText: LongTextEditor
  };
  window.Slick && Utils.extend(Slick, {
    Editors
  });
})();
//# sourceMappingURL=slick.editors.js.map