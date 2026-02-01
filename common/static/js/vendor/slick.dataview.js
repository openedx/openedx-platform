"use strict";
(() => {
  var __defProp = Object.defineProperty;
  var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: !0, configurable: !0, writable: !0, value }) : obj[key] = value;
  var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key != "symbol" ? key + "" : key, value);

  // src/slick.dataview.ts
  var SlickEvent = Slick.Event, SlickEventData = Slick.EventData, SlickGroup = Slick.Group, SlickGroupTotals = Slick.GroupTotals, Utils = Slick.Utils, _a, _b, SlickGroupItemMetadataProvider = (_b = (_a = Slick.Data) == null ? void 0 : _a.GroupItemMetadataProvider) != null ? _b : {}, SlickDataView = class {
    constructor(options, externalPubSub) {
      this.externalPubSub = externalPubSub;
      __publicField(this, "defaults", {
        globalItemMetadataProvider: null,
        groupItemMetadataProvider: null,
        inlineFilters: !1,
        useCSPSafeFilter: !1
      });
      // private
      __publicField(this, "idProperty", "id");
      // property holding a unique row id
      __publicField(this, "items", []);
      // data by index
      __publicField(this, "rows", []);
      // data by row
      __publicField(this, "idxById", /* @__PURE__ */ new Map());
      // indexes by id
      __publicField(this, "rowsById");
      // rows by id; lazy-calculated
      __publicField(this, "filter", null);
      // filter function
      __publicField(this, "filterCSPSafe", null);
      // filter function
      __publicField(this, "updated", null);
      // updated item ids
      __publicField(this, "suspend", !1);
      // suspends the recalculation
      __publicField(this, "isBulkSuspend", !1);
      // delays protectedious operations like the
      // index update and delete to efficient
      // versions at endUpdate
      __publicField(this, "bulkDeleteIds", /* @__PURE__ */ new Map());
      __publicField(this, "sortAsc", !0);
      __publicField(this, "fastSortField");
      __publicField(this, "sortComparer");
      __publicField(this, "refreshHints", {});
      __publicField(this, "prevRefreshHints", {});
      __publicField(this, "filterArgs");
      __publicField(this, "filteredItems", []);
      __publicField(this, "compiledFilter");
      __publicField(this, "compiledFilterCSPSafe");
      __publicField(this, "compiledFilterWithCaching");
      __publicField(this, "compiledFilterWithCachingCSPSafe");
      __publicField(this, "filterCache", []);
      __publicField(this, "_grid");
      // grid object will be defined only after using "syncGridSelection()" method"
      // grouping
      __publicField(this, "groupingInfoDefaults", {
        getter: void 0,
        formatter: void 0,
        comparer: (a, b) => a.value === b.value ? 0 : a.value > b.value ? 1 : -1,
        predefinedValues: [],
        aggregators: [],
        aggregateEmpty: !1,
        aggregateCollapsed: !1,
        aggregateChildGroups: !1,
        collapsed: !1,
        displayTotalsRow: !0,
        lazyTotalsCalculation: !1
      });
      __publicField(this, "groupingInfos", []);
      __publicField(this, "groups", []);
      __publicField(this, "toggledGroupsByLevel", []);
      __publicField(this, "groupingDelimiter", ":|:");
      __publicField(this, "selectedRowIds", []);
      __publicField(this, "preSelectedRowIdsChangeFn");
      __publicField(this, "pagesize", 0);
      __publicField(this, "pagenum", 0);
      __publicField(this, "totalRows", 0);
      __publicField(this, "_options");
      __publicField(this, "_container");
      // public events
      __publicField(this, "onBeforePagingInfoChanged");
      __publicField(this, "onGroupExpanded");
      __publicField(this, "onGroupCollapsed");
      __publicField(this, "onPagingInfoChanged");
      __publicField(this, "onRowCountChanged");
      __publicField(this, "onRowsChanged");
      __publicField(this, "onRowsOrCountChanged");
      __publicField(this, "onSelectedRowIdsChanged");
      __publicField(this, "onSetItemsCalled");
      this.onBeforePagingInfoChanged = new SlickEvent("onBeforePagingInfoChanged", externalPubSub), this.onGroupExpanded = new SlickEvent("onGroupExpanded", externalPubSub), this.onGroupCollapsed = new SlickEvent("onGroupCollapsed", externalPubSub), this.onPagingInfoChanged = new SlickEvent("onPagingInfoChanged", externalPubSub), this.onRowCountChanged = new SlickEvent("onRowCountChanged", externalPubSub), this.onRowsChanged = new SlickEvent("onRowsChanged", externalPubSub), this.onRowsOrCountChanged = new SlickEvent("onRowsOrCountChanged", externalPubSub), this.onSelectedRowIdsChanged = new SlickEvent("onSelectedRowIdsChanged", externalPubSub), this.onSetItemsCalled = new SlickEvent("onSetItemsCalled", externalPubSub), this._options = Utils.extend(!0, {}, this.defaults, options);
    }
    /**
     * Begins a bached update of the items in the data view.
     * including deletes and the related events are postponed to the endUpdate call.
     * As certain operations are postponed during this update, some methods might not
     * deliver fully consistent information.
     * @param {Boolean} [bulkUpdate] - if set to true, most data view modifications
     */
    beginUpdate(bulkUpdate) {
      this.suspend = !0, this.isBulkSuspend = bulkUpdate === !0;
    }
    endUpdate() {
      let wasBulkSuspend = this.isBulkSuspend;
      this.isBulkSuspend = !1, this.suspend = !1, wasBulkSuspend && (this.processBulkDelete(), this.ensureIdUniqueness()), this.refresh();
    }
    destroy() {
      this.items = [], this.idxById = null, this.rowsById = null, this.filter = null, this.filterCSPSafe = null, this.updated = null, this.sortComparer = null, this.filterCache = [], this.filteredItems = [], this.compiledFilter = null, this.compiledFilterCSPSafe = null, this.compiledFilterWithCaching = null, this.compiledFilterWithCachingCSPSafe = null, this._grid && this._grid.onSelectedRowsChanged && this._grid.onCellCssStylesChanged && (this._grid.onSelectedRowsChanged.unsubscribe(), this._grid.onCellCssStylesChanged.unsubscribe()), this.onRowsOrCountChanged && this.onRowsOrCountChanged.unsubscribe();
    }
    /** provide some refresh hints as to what to rows needs refresh */
    setRefreshHints(hints) {
      this.refreshHints = hints;
    }
    /** get extra filter arguments of the filter method */
    getFilterArgs() {
      return this.filterArgs;
    }
    /** add extra filter arguments to the filter method */
    setFilterArgs(args) {
      this.filterArgs = args;
    }
    /**
     * Processes all delete requests placed during bulk update
     * by recomputing the items and idxById members.
     */
    processBulkDelete() {
      if (!this.idxById)
        return;
      let id, item, newIdx = 0;
      for (let i = 0, l = this.items.length; i < l; i++) {
        if (item = this.items[i], id = item[this.idProperty], id === void 0)
          throw new Error("[SlickGrid DataView] Each data element must implement a unique 'id' property");
        this.bulkDeleteIds.has(id) ? this.idxById.delete(id) : (this.items[newIdx] = item, this.idxById.set(id, newIdx), ++newIdx);
      }
      this.items.length = newIdx, this.bulkDeleteIds = /* @__PURE__ */ new Map();
    }
    updateIdxById(startingIndex) {
      if (this.isBulkSuspend || !this.idxById)
        return;
      startingIndex = startingIndex || 0;
      let id;
      for (let i = startingIndex, l = this.items.length; i < l; i++) {
        if (id = this.items[i][this.idProperty], id === void 0)
          throw new Error("[SlickGrid DataView] Each data element must implement a unique 'id' property");
        this.idxById.set(id, i);
      }
    }
    ensureIdUniqueness() {
      if (this.isBulkSuspend || !this.idxById)
        return;
      let id;
      for (let i = 0, l = this.items.length; i < l; i++)
        if (id = this.items[i][this.idProperty], id === void 0 || this.idxById.get(id) !== i)
          throw new Error("[SlickGrid DataView] Each data element must implement a unique 'id' property");
    }
    /** Get all DataView Items */
    getItems() {
      return this.items;
    }
    /** Get the DataView Id property name to use (defaults to "Id" but could be customized to something else when instantiating the DataView) */
    getIdPropertyName() {
      return this.idProperty;
    }
    /**
     * Set the Items with a new Dataset and optionally pass a different Id property name
     * @param {Array<*>} data - array of data
     * @param {String} [objectIdProperty] - optional id property to use as primary id
     */
    setItems(data, objectIdProperty) {
      objectIdProperty !== void 0 && (this.idProperty = objectIdProperty), this.items = this.filteredItems = data, this.onSetItemsCalled.notify({ idProperty: this.idProperty, itemCount: this.items.length }, null, this), this.idxById = /* @__PURE__ */ new Map(), this.updateIdxById(), this.ensureIdUniqueness(), this.refresh();
    }
    /** Set Paging Options */
    setPagingOptions(args) {
      this.onBeforePagingInfoChanged.notify(this.getPagingInfo(), null, this).getReturnValue() !== !1 && (Utils.isDefined(args.pageSize) && (this.pagesize = args.pageSize, this.pagenum = this.pagesize ? Math.min(this.pagenum, Math.max(0, Math.ceil(this.totalRows / this.pagesize) - 1)) : 0), Utils.isDefined(args.pageNum) && (this.pagenum = Math.min(args.pageNum, Math.max(0, Math.ceil(this.totalRows / this.pagesize) - 1))), this.onPagingInfoChanged.notify(this.getPagingInfo(), null, this), this.refresh());
    }
    /** Get Paging Options */
    getPagingInfo() {
      let totalPages = this.pagesize ? Math.max(1, Math.ceil(this.totalRows / this.pagesize)) : 1;
      return { pageSize: this.pagesize, pageNum: this.pagenum, totalRows: this.totalRows, totalPages, dataView: this };
    }
    /** Sort Method to use by the DataView */
    sort(comparer, ascending) {
      this.sortAsc = ascending, this.sortComparer = comparer, this.fastSortField = null, ascending === !1 && this.items.reverse(), this.items.sort(comparer), ascending === !1 && this.items.reverse(), this.idxById = /* @__PURE__ */ new Map(), this.updateIdxById(), this.refresh();
    }
    /**
     * @deprecated, to be more removed in next major since IE is no longer supported and this is no longer useful.
     * Provides a workaround for the extremely slow sorting in IE.
     * Does a [lexicographic] sort on a give column by temporarily overriding Object.prototype.toString
     * to return the value of that field and then doing a native Array.sort().
     */
    fastSort(field, ascending) {
      this.sortAsc = ascending, this.fastSortField = field, this.sortComparer = null;
      let oldToString = Object.prototype.toString;
      Object.prototype.toString = typeof field == "function" ? field : function() {
        return this[field];
      }, ascending === !1 && this.items.reverse(), this.items.sort(), Object.prototype.toString = oldToString, ascending === !1 && this.items.reverse(), this.idxById = /* @__PURE__ */ new Map(), this.updateIdxById(), this.refresh();
    }
    /** Re-Sort the dataset */
    reSort() {
      this.sortComparer ? this.sort(this.sortComparer, this.sortAsc) : this.fastSortField && this.fastSort(this.fastSortField, this.sortAsc);
    }
    /** Get only the DataView filtered items */
    getFilteredItems() {
      return this.filteredItems;
    }
    /** Get the array length (count) of only the DataView filtered items */
    getFilteredItemCount() {
      return this.filteredItems.length;
    }
    /** Get current Filter used by the DataView */
    getFilter() {
      return this._options.useCSPSafeFilter ? this.filterCSPSafe : this.filter;
    }
    /**
     * Set a Filter that will be used by the DataView
     * @param {Function} fn - filter callback function
     */
    setFilter(filterFn) {
      this.filterCSPSafe = filterFn, this.filter = filterFn, this._options.inlineFilters && (this.compiledFilterCSPSafe = this.compileFilterCSPSafe, this.compiledFilterWithCachingCSPSafe = this.compileFilterWithCachingCSPSafe, this.compiledFilter = this.compileFilter(this._options.useCSPSafeFilter), this.compiledFilterWithCaching = this.compileFilterWithCaching(this._options.useCSPSafeFilter)), this.refresh();
    }
    /** Get current Grouping info */
    getGrouping() {
      return this.groupingInfos;
    }
    /** Set some Grouping */
    setGrouping(groupingInfo) {
      this._options.groupItemMetadataProvider || (this._options.groupItemMetadataProvider = new SlickGroupItemMetadataProvider()), this.groups = [], this.toggledGroupsByLevel = [], groupingInfo = groupingInfo || [], this.groupingInfos = groupingInfo instanceof Array ? groupingInfo : [groupingInfo];
      for (let i = 0; i < this.groupingInfos.length; i++) {
        let gi = this.groupingInfos[i] = Utils.extend(!0, {}, this.groupingInfoDefaults, this.groupingInfos[i]);
        gi.getterIsAFn = typeof gi.getter == "function", gi.compiledAccumulators = [];
        let idx = gi.aggregators.length;
        for (; idx--; )
          gi.compiledAccumulators[idx] = this.compileAccumulatorLoopCSPSafe(gi.aggregators[idx]);
        this.toggledGroupsByLevel[i] = {};
      }
      this.refresh();
    }
    /** Get an item in the DataView by its row index */
    getItemByIdx(i) {
      return this.items[i];
    }
    /** Get row index in the DataView by its Id */
    getIdxById(id) {
      var _a2;
      return (_a2 = this.idxById) == null ? void 0 : _a2.get(id);
    }
    ensureRowsByIdCache() {
      if (!this.rowsById) {
        this.rowsById = {};
        for (let i = 0, l = this.rows.length; i < l; i++)
          this.rowsById[this.rows[i][this.idProperty]] = i;
      }
    }
    /** Get row number in the grid by its item object */
    getRowByItem(item) {
      var _a2;
      return this.ensureRowsByIdCache(), (_a2 = this.rowsById) == null ? void 0 : _a2[item[this.idProperty]];
    }
    /** Get row number in the grid by its Id */
    getRowById(id) {
      var _a2;
      return this.ensureRowsByIdCache(), (_a2 = this.rowsById) == null ? void 0 : _a2[id];
    }
    /** Get an item in the DataView by its Id */
    getItemById(id) {
      return this.items[this.idxById.get(id)];
    }
    /** From the items array provided, return the mapped rows */
    mapItemsToRows(itemArray) {
      var _a2;
      let rows = [];
      this.ensureRowsByIdCache();
      for (let i = 0, l = itemArray.length; i < l; i++) {
        let row = (_a2 = this.rowsById) == null ? void 0 : _a2[itemArray[i][this.idProperty]];
        Utils.isDefined(row) && (rows[rows.length] = row);
      }
      return rows;
    }
    /** From the Ids array provided, return the mapped rows */
    mapIdsToRows(idArray) {
      var _a2;
      let rows = [];
      this.ensureRowsByIdCache();
      for (let i = 0, l = idArray.length; i < l; i++) {
        let row = (_a2 = this.rowsById) == null ? void 0 : _a2[idArray[i]];
        Utils.isDefined(row) && (rows[rows.length] = row);
      }
      return rows;
    }
    /** From the rows array provided, return the mapped Ids */
    mapRowsToIds(rowArray) {
      let ids = [];
      for (let i = 0, l = rowArray.length; i < l; i++)
        if (rowArray[i] < this.rows.length) {
          let rowItem = this.rows[rowArray[i]];
          ids[ids.length] = rowItem[this.idProperty];
        }
      return ids;
    }
    /**
     * Performs the update operations of a single item by id without
     * triggering any events or refresh operations.
     * @param id The new id of the item.
     * @param item The item which should be the new value for the given id.
     */
    updateSingleItem(id, item) {
      var _a2;
      if (this.idxById) {
        if (!this.idxById.has(id))
          throw new Error("[SlickGrid DataView] Invalid id");
        if (id !== item[this.idProperty]) {
          let newId = item[this.idProperty];
          if (!Utils.isDefined(newId))
            throw new Error("[SlickGrid DataView] Cannot update item to associate with a null id");
          if (this.idxById.has(newId))
            throw new Error("[SlickGrid DataView] Cannot update item to associate with a non-unique id");
          this.idxById.set(newId, this.idxById.get(id)), this.idxById.delete(id), (_a2 = this.updated) != null && _a2[id] && delete this.updated[id], id = newId;
        }
        this.items[this.idxById.get(id)] = item, this.updated || (this.updated = {}), this.updated[id] = !0;
      }
    }
    /**
     * Updates a single item in the data view given the id and new value.
     * @param id The new id of the item.
     * @param item The item which should be the new value for the given id.
     */
    updateItem(id, item) {
      this.updateSingleItem(id, item), this.refresh();
    }
    /**
     * Updates multiple items in the data view given the new ids and new values.
     * @param id {Array} The array of new ids which is in the same order as the items.
     * @param newItems {Array} The new items that should be set in the data view for the given ids.
     */
    updateItems(ids, newItems) {
      if (ids.length !== newItems.length)
        throw new Error("[SlickGrid DataView] Mismatch on the length of ids and items provided to update");
      for (let i = 0, l = newItems.length; i < l; i++)
        this.updateSingleItem(ids[i], newItems[i]);
      this.refresh();
    }
    /**
     * Inserts a single item into the data view at the given position.
     * @param insertBefore {Number} The 0-based index before which the item should be inserted.
     * @param item The item to insert.
     */
    insertItem(insertBefore, item) {
      this.items.splice(insertBefore, 0, item), this.updateIdxById(insertBefore), this.refresh();
    }
    /**
     * Inserts multiple items into the data view at the given position.
     * @param insertBefore {Number} The 0-based index before which the items should be inserted.
     * @param newItems {Array}  The items to insert.
     */
    insertItems(insertBefore, newItems) {
      Array.prototype.splice.apply(this.items, [insertBefore, 0].concat(newItems)), this.updateIdxById(insertBefore), this.refresh();
    }
    /**
     * Adds a single item at the end of the data view.
     * @param item The item to add at the end.
     */
    addItem(item) {
      this.items.push(item), this.updateIdxById(this.items.length - 1), this.refresh();
    }
    /**
     * Adds multiple items at the end of the data view.
     * @param {Array} newItems The items to add at the end.
     */
    addItems(newItems) {
      this.items = this.items.concat(newItems), this.updateIdxById(this.items.length - newItems.length), this.refresh();
    }
    /**
     * Deletes a single item identified by the given id from the data view.
     * @param {String|Number} id The id identifying the object to delete.
     */
    deleteItem(id) {
      if (this.idxById)
        if (this.isBulkSuspend)
          this.bulkDeleteIds.set(id, !0);
        else {
          let idx = this.idxById.get(id);
          if (idx === void 0)
            throw new Error("[SlickGrid DataView] Invalid id");
          this.idxById.delete(id), this.items.splice(idx, 1), this.updateIdxById(idx), this.refresh();
        }
    }
    /**
     * Deletes multiple item identified by the given ids from the data view.
     * @param {Array} ids The ids of the items to delete.
     */
    deleteItems(ids) {
      if (!(ids.length === 0 || !this.idxById))
        if (this.isBulkSuspend)
          for (let i = 0, l = ids.length; i < l; i++) {
            let id = ids[i];
            if (this.idxById.get(id) === void 0)
              throw new Error("[SlickGrid DataView] Invalid id");
            this.bulkDeleteIds.set(id, !0);
          }
        else {
          let indexesToDelete = [];
          for (let i = 0, l = ids.length; i < l; i++) {
            let id = ids[i], idx = this.idxById.get(id);
            if (idx === void 0)
              throw new Error("[SlickGrid DataView] Invalid id");
            this.idxById.delete(id), indexesToDelete.push(idx);
          }
          indexesToDelete.sort();
          for (let i = indexesToDelete.length - 1; i >= 0; --i)
            this.items.splice(indexesToDelete[i], 1);
          this.updateIdxById(indexesToDelete[0]), this.refresh();
        }
    }
    /** Add an item in a sorted dataset (a Sort function must be defined) */
    sortedAddItem(item) {
      if (!this.sortComparer)
        throw new Error("[SlickGrid DataView] sortedAddItem() requires a sort comparer, use sort()");
      this.insertItem(this.sortedIndex(item), item);
    }
    /** Update an item in a sorted dataset (a Sort function must be defined) */
    sortedUpdateItem(id, item) {
      if (!this.idxById)
        return;
      if (!this.idxById.has(id) || id !== item[this.idProperty])
        throw new Error("[SlickGrid DataView] Invalid or non-matching id " + this.idxById.get(id));
      if (!this.sortComparer)
        throw new Error("[SlickGrid DataView] sortedUpdateItem() requires a sort comparer, use sort()");
      let oldItem = this.getItemById(id);
      this.sortComparer(oldItem, item) !== 0 ? (this.deleteItem(id), this.sortedAddItem(item)) : this.updateItem(id, item);
    }
    sortedIndex(searchItem) {
      let low = 0, high = this.items.length;
      for (; low < high; ) {
        let mid = low + high >>> 1;
        this.sortComparer(this.items[mid], searchItem) === -1 ? low = mid + 1 : high = mid;
      }
      return low;
    }
    /** Get item count, that is the full dataset lenght of the DataView */
    getItemCount() {
      return this.items.length;
    }
    /** Get row count (rows displayed in current page) */
    getLength() {
      return this.rows.length;
    }
    /** Retrieve an item from the DataView at specific index */
    getItem(i) {
      var _a2;
      let item = this.rows[i];
      if (item != null && item.__group && item.totals && !((_a2 = item.totals) != null && _a2.initialized)) {
        let gi = this.groupingInfos[item.level];
        gi.displayTotalsRow || (this.calculateTotals(item.totals), item.title = gi.formatter ? gi.formatter(item) : item.value);
      } else item != null && item.__groupTotals && !item.initialized && this.calculateTotals(item);
      return item;
    }
    getItemMetadata(row) {
      var _a2, _b2, _c;
      let item = this.rows[row];
      return item === void 0 ? null : (_a2 = this._options.globalItemMetadataProvider) != null && _a2.getRowMetadata ? this._options.globalItemMetadataProvider.getRowMetadata(item, row) : item.__group && ((_b2 = this._options.groupItemMetadataProvider) != null && _b2.getGroupRowMetadata) ? this._options.groupItemMetadataProvider.getGroupRowMetadata(item, row) : item.__groupTotals && ((_c = this._options.groupItemMetadataProvider) != null && _c.getTotalsRowMetadata) ? this._options.groupItemMetadataProvider.getTotalsRowMetadata(item, row) : null;
    }
    expandCollapseAllGroups(level, collapse) {
      if (Utils.isDefined(level))
        this.toggledGroupsByLevel[level] = {}, this.groupingInfos[level].collapsed = collapse, collapse === !0 ? this.onGroupCollapsed.notify({ level, groupingKey: null }) : this.onGroupExpanded.notify({ level, groupingKey: null });
      else
        for (let i = 0; i < this.groupingInfos.length; i++)
          this.toggledGroupsByLevel[i] = {}, this.groupingInfos[i].collapsed = collapse, collapse === !0 ? this.onGroupCollapsed.notify({ level: i, groupingKey: null }) : this.onGroupExpanded.notify({ level: i, groupingKey: null });
      this.refresh();
    }
    /**
     * @param {Number} [level] Optional level to collapse.  If not specified, applies to all levels.
     */
    collapseAllGroups(level) {
      this.expandCollapseAllGroups(level, !0);
    }
    /**
     * @param {Number} [level] Optional level to expand.  If not specified, applies to all levels.
     */
    expandAllGroups(level) {
      this.expandCollapseAllGroups(level, !1);
    }
    expandCollapseGroup(level, groupingKey, collapse) {
      this.toggledGroupsByLevel[level][groupingKey] = this.groupingInfos[level].collapsed ^ collapse, this.refresh();
    }
    /**
     * @param varArgs Either a Slick.Group's "groupingKey" property, or a
     *     variable argument list of grouping values denoting a unique path to the row.  For
     *     example, calling collapseGroup('high', '10%') will collapse the '10%' subgroup of
     *     the 'high' group.
     */
    collapseGroup(...args) {
      let arg0 = Array.prototype.slice.call(args)[0], groupingKey, level;
      args.length === 1 && arg0.indexOf(this.groupingDelimiter) !== -1 ? (groupingKey = arg0, level = arg0.split(this.groupingDelimiter).length - 1) : (groupingKey = args.join(this.groupingDelimiter), level = args.length - 1), this.expandCollapseGroup(level, groupingKey, !0), this.onGroupCollapsed.notify({ level, groupingKey });
    }
    /**
     * @param varArgs Either a Slick.Group's "groupingKey" property, or a
     *     variable argument list of grouping values denoting a unique path to the row.  For
     *     example, calling expandGroup('high', '10%') will expand the '10%' subgroup of
     *     the 'high' group.
     */
    expandGroup(...args) {
      let arg0 = Array.prototype.slice.call(args)[0], groupingKey, level;
      args.length === 1 && arg0.indexOf(this.groupingDelimiter) !== -1 ? (level = arg0.split(this.groupingDelimiter).length - 1, groupingKey = arg0) : (level = args.length - 1, groupingKey = args.join(this.groupingDelimiter)), this.expandCollapseGroup(level, groupingKey, !1), this.onGroupExpanded.notify({ level, groupingKey });
    }
    getGroups() {
      return this.groups;
    }
    extractGroups(rows, parentGroup) {
      var _a2, _b2, _c;
      let group, val, groups = [], groupsByVal = {}, r, level = parentGroup ? parentGroup.level + 1 : 0, gi = this.groupingInfos[level];
      for (let i = 0, l = (_b2 = (_a2 = gi.predefinedValues) == null ? void 0 : _a2.length) != null ? _b2 : 0; i < l; i++)
        val = (_c = gi.predefinedValues) == null ? void 0 : _c[i], group = groupsByVal[val], group || (group = new SlickGroup(), group.value = val, group.level = level, group.groupingKey = (parentGroup ? parentGroup.groupingKey + this.groupingDelimiter : "") + val, groups[groups.length] = group, groupsByVal[val] = group);
      for (let i = 0, l = rows.length; i < l; i++)
        r = rows[i], val = gi.getterIsAFn ? gi.getter(r) : r[gi.getter], group = groupsByVal[val], group || (group = new SlickGroup(), group.value = val, group.level = level, group.groupingKey = (parentGroup ? parentGroup.groupingKey + this.groupingDelimiter : "") + val, groups[groups.length] = group, groupsByVal[val] = group), group.rows[group.count++] = r;
      if (level < this.groupingInfos.length - 1)
        for (let i = 0; i < groups.length; i++)
          group = groups[i], group.groups = this.extractGroups(group.rows, group);
      return groups.length && this.addTotals(groups, level), groups.sort(this.groupingInfos[level].comparer), groups;
    }
    /** claculate Group Totals */
    calculateTotals(totals) {
      var _a2, _b2, _c;
      let group = totals.group, gi = this.groupingInfos[(_a2 = group.level) != null ? _a2 : 0], isLeafLevel = group.level === this.groupingInfos.length, agg, idx = gi.aggregators.length;
      if (!isLeafLevel && gi.aggregateChildGroups) {
        let i = (_c = (_b2 = group.groups) == null ? void 0 : _b2.length) != null ? _c : 0;
        for (; i--; )
          group.groups[i].totals.initialized || this.calculateTotals(group.groups[i].totals);
      }
      for (; idx--; )
        agg = gi.aggregators[idx], agg.init(), !isLeafLevel && gi.aggregateChildGroups ? gi.compiledAccumulators[idx].call(agg, group.groups) : gi.compiledAccumulators[idx].call(agg, group.rows), agg.storeResult(totals);
      totals.initialized = !0;
    }
    addGroupTotals(group) {
      let gi = this.groupingInfos[group.level], totals = new SlickGroupTotals();
      totals.group = group, group.totals = totals, gi.lazyTotalsCalculation || this.calculateTotals(totals);
    }
    addTotals(groups, level) {
      var _a2, _b2;
      level = level || 0;
      let gi = this.groupingInfos[level], groupCollapsed = gi.collapsed, toggledGroups = this.toggledGroupsByLevel[level], idx = groups.length, g;
      for (; idx--; )
        g = groups[idx], !(g.collapsed && !gi.aggregateCollapsed) && (g.groups && this.addTotals(g.groups, level + 1), (_a2 = gi.aggregators) != null && _a2.length && (gi.aggregateEmpty || g.rows.length || (_b2 = g.groups) != null && _b2.length) && this.addGroupTotals(g), g.collapsed = groupCollapsed ^ toggledGroups[g.groupingKey], g.title = gi.formatter ? gi.formatter(g) : g.value);
    }
    flattenGroupedRows(groups, level) {
      level = level || 0;
      let gi = this.groupingInfos[level], groupedRows = [], rows, gl = 0, g;
      for (let i = 0, l = groups.length; i < l; i++) {
        if (g = groups[i], groupedRows[gl++] = g, !g.collapsed) {
          rows = g.groups ? this.flattenGroupedRows(g.groups, level + 1) : g.rows;
          for (let j = 0, jj = rows.length; j < jj; j++)
            groupedRows[gl++] = rows[j];
        }
        g.totals && gi.displayTotalsRow && (!g.collapsed || gi.aggregateCollapsed) && (groupedRows[gl++] = g.totals);
      }
      return groupedRows;
    }
    compileAccumulatorLoopCSPSafe(aggregator) {
      return aggregator.accumulate ? function(items) {
        let result;
        for (let i = 0; i < items.length; i++) {
          let item = items[i];
          result = aggregator.accumulate.call(aggregator, item);
        }
        return result;
      } : function() {
      };
    }
    compileFilterCSPSafe(items, args) {
      if (typeof this.filterCSPSafe != "function")
        return [];
      let _retval = [], _il = items.length;
      for (let _i = 0; _i < _il; _i++)
        this.filterCSPSafe(items[_i], args) && _retval.push(items[_i]);
      return _retval;
    }
    compileFilter(stopRunningIfCSPSafeIsActive = !1) {
      if (stopRunningIfCSPSafeIsActive)
        return null;
      let filterInfo = Utils.getFunctionDetails(this.filter), filterPath1 = "{ continue _coreloop; }$1", filterPath2 = "{ _retval[_idx++] = $item$; continue _coreloop; }$1", filterBody = filterInfo.body.replace(/return false\s*([;}]|\}|$)/gi, filterPath1).replace(/return!1([;}]|\}|$)/gi, filterPath1).replace(/return true\s*([;}]|\}|$)/gi, filterPath2).replace(/return!0([;}]|\}|$)/gi, filterPath2).replace(
        /return ([^;}]+?)\s*([;}]|$)/gi,
        "{ if ($1) { _retval[_idx++] = $item$; }; continue _coreloop; }$2"
      ), tpl = [
        // 'function(_items, _args) { ',
        "var _retval = [], _idx = 0; ",
        "var $item$, $args$ = _args; ",
        "_coreloop: ",
        "for (var _i = 0, _il = _items.length; _i < _il; _i++) { ",
        "$item$ = _items[_i]; ",
        "$filter$; ",
        "} ",
        "return _retval; "
        // '}'
      ].join("");
      tpl = tpl.replace(/\$filter\$/gi, filterBody), tpl = tpl.replace(/\$item\$/gi, filterInfo.params[0]), tpl = tpl.replace(/\$args\$/gi, filterInfo.params[1]);
      let fn = new Function("_items,_args", tpl), fnName = "compiledFilter";
      return fn.displayName = fnName, fn.name = this.setFunctionName(fn, fnName), fn;
    }
    compileFilterWithCaching(stopRunningIfCSPSafeIsActive = !1) {
      if (stopRunningIfCSPSafeIsActive)
        return null;
      let filterInfo = Utils.getFunctionDetails(this.filter), filterPath1 = "{ continue _coreloop; }$1", filterPath2 = "{ _cache[_i] = true;_retval[_idx++] = $item$; continue _coreloop; }$1", filterBody = filterInfo.body.replace(/return false\s*([;}]|\}|$)/gi, filterPath1).replace(/return!1([;}]|\}|$)/gi, filterPath1).replace(/return true\s*([;}]|\}|$)/gi, filterPath2).replace(/return!0([;}]|\}|$)/gi, filterPath2).replace(
        /return ([^;}]+?)\s*([;}]|$)/gi,
        "{ if ((_cache[_i] = $1)) { _retval[_idx++] = $item$; }; continue _coreloop; }$2"
      ), tpl = [
        // 'function(_items, _args, _cache) { ',
        "var _retval = [], _idx = 0; ",
        "var $item$, $args$ = _args; ",
        "_coreloop: ",
        "for (var _i = 0, _il = _items.length; _i < _il; _i++) { ",
        "$item$ = _items[_i]; ",
        "if (_cache[_i]) { ",
        "_retval[_idx++] = $item$; ",
        "continue _coreloop; ",
        "} ",
        "$filter$; ",
        "} ",
        "return _retval; "
        // '}'
      ].join("");
      tpl = tpl.replace(/\$filter\$/gi, filterBody), tpl = tpl.replace(/\$item\$/gi, filterInfo.params[0]), tpl = tpl.replace(/\$args\$/gi, filterInfo.params[1]);
      let fn = new Function("_items,_args,_cache", tpl), fnName = "compiledFilterWithCaching";
      return fn.displayName = fnName, fn.name = this.setFunctionName(fn, fnName), fn;
    }
    compileFilterWithCachingCSPSafe(items, args, filterCache) {
      if (typeof this.filterCSPSafe != "function")
        return [];
      let retval = [], il = items.length;
      for (let _i = 0; _i < il; _i++)
        (filterCache[_i] || this.filterCSPSafe(items[_i], args)) && retval.push(items[_i]);
      return retval;
    }
    /**
     * In ES5 we could set the function name on the fly but in ES6 this is forbidden and we need to set it through differently
     * We can use Object.defineProperty and set it the property to writable, see MDN for reference
     * https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Object/defineProperty
     * @param {*} fn
     * @param {string} fnName
     */
    setFunctionName(fn, fnName) {
      try {
        Object.defineProperty(fn, "name", { writable: !0, value: fnName });
      } catch (err) {
        fn.name = fnName;
      }
    }
    uncompiledFilter(items, args) {
      var _a2;
      let retval = [], idx = 0;
      for (let i = 0, ii = items.length; i < ii; i++)
        (_a2 = this.filter) != null && _a2.call(this, items[i], args) && (retval[idx++] = items[i]);
      return retval;
    }
    uncompiledFilterWithCaching(items, args, cache) {
      var _a2;
      let retval = [], idx = 0, item;
      for (let i = 0, ii = items.length; i < ii; i++)
        item = items[i], cache[i] ? retval[idx++] = item : (_a2 = this.filter) != null && _a2.call(this, item, args) && (retval[idx++] = item, cache[i] = !0);
      return retval;
    }
    getFilteredAndPagedItems(items) {
      if (this._options.useCSPSafeFilter ? this.filterCSPSafe : this.filter) {
        let batchFilter, batchFilterWithCaching;
        this._options.useCSPSafeFilter ? (batchFilter = this._options.inlineFilters ? this.compiledFilterCSPSafe : this.uncompiledFilter, batchFilterWithCaching = this._options.inlineFilters ? this.compiledFilterWithCachingCSPSafe : this.uncompiledFilterWithCaching) : (batchFilter = this._options.inlineFilters ? this.compiledFilter : this.uncompiledFilter, batchFilterWithCaching = this._options.inlineFilters ? this.compiledFilterWithCaching : this.uncompiledFilterWithCaching), this.refreshHints.isFilterNarrowing ? this.filteredItems = batchFilter.call(this, this.filteredItems, this.filterArgs) : this.refreshHints.isFilterExpanding ? this.filteredItems = batchFilterWithCaching.call(this, items, this.filterArgs, this.filterCache) : this.refreshHints.isFilterUnchanged || (this.filteredItems = batchFilter.call(this, items, this.filterArgs));
      } else
        this.filteredItems = this.pagesize ? items : items.concat();
      let paged;
      return this.pagesize ? (this.filteredItems.length <= this.pagenum * this.pagesize && (this.filteredItems.length === 0 ? this.pagenum = 0 : this.pagenum = Math.floor((this.filteredItems.length - 1) / this.pagesize)), paged = this.filteredItems.slice(this.pagesize * this.pagenum, this.pagesize * this.pagenum + this.pagesize)) : paged = this.filteredItems, { totalRows: this.filteredItems.length, rows: paged };
    }
    getRowDiffs(rows, newRows) {
      var _a2, _b2, _c;
      let item, r, eitherIsNonData, diff = [], from = 0, to = Math.max(newRows.length, rows.length);
      (_a2 = this.refreshHints) != null && _a2.ignoreDiffsBefore && (from = Math.max(
        0,
        Math.min(newRows.length, this.refreshHints.ignoreDiffsBefore)
      )), (_b2 = this.refreshHints) != null && _b2.ignoreDiffsAfter && (to = Math.min(
        newRows.length,
        Math.max(0, this.refreshHints.ignoreDiffsAfter)
      ));
      for (let i = from, rl = rows.length; i < to; i++)
        i >= rl ? diff[diff.length] = i : (item = newRows[i], r = rows[i], (!item || this.groupingInfos.length && (eitherIsNonData = item.__nonDataRow || r.__nonDataRow) && item.__group !== r.__group || item.__group && !item.equals(r) || eitherIsNonData && // no good way to compare totals since they are arbitrary DTOs
        // deep object comparison is pretty expensive
        // always considering them 'dirty' seems easier for the time being
        (item.__groupTotals || r.__groupTotals) || item[this.idProperty] !== r[this.idProperty] || (_c = this.updated) != null && _c[item[this.idProperty]]) && (diff[diff.length] = i));
      return diff;
    }
    recalc(_items) {
      this.rowsById = void 0, (this.refreshHints.isFilterNarrowing !== this.prevRefreshHints.isFilterNarrowing || this.refreshHints.isFilterExpanding !== this.prevRefreshHints.isFilterExpanding) && (this.filterCache = []);
      let filteredItems = this.getFilteredAndPagedItems(_items);
      this.totalRows = filteredItems.totalRows;
      let newRows = filteredItems.rows;
      this.groups = [], this.groupingInfos.length && (this.groups = this.extractGroups(newRows), this.groups.length && (newRows = this.flattenGroupedRows(this.groups)));
      let diff = this.getRowDiffs(this.rows, newRows);
      return this.rows = newRows, diff;
    }
    refresh() {
      if (this.suspend)
        return;
      let previousPagingInfo = Utils.extend(!0, {}, this.getPagingInfo()), countBefore = this.rows.length, totalRowsBefore = this.totalRows, diff = this.recalc(this.items);
      this.pagesize && this.totalRows < this.pagenum * this.pagesize && (this.pagenum = Math.max(0, Math.ceil(this.totalRows / this.pagesize) - 1), diff = this.recalc(this.items)), this.updated = null, this.prevRefreshHints = this.refreshHints, this.refreshHints = {}, totalRowsBefore !== this.totalRows && this.onBeforePagingInfoChanged.notify(previousPagingInfo, null, this).getReturnValue() !== !1 && this.onPagingInfoChanged.notify(this.getPagingInfo(), null, this), countBefore !== this.rows.length && this.onRowCountChanged.notify({ previous: countBefore, current: this.rows.length, itemCount: this.items.length, dataView: this, callingOnRowsChanged: diff.length > 0 }, null, this), diff.length > 0 && this.onRowsChanged.notify({ rows: diff, itemCount: this.items.length, dataView: this, calledOnRowCountChanged: countBefore !== this.rows.length }, null, this), (countBefore !== this.rows.length || diff.length > 0) && this.onRowsOrCountChanged.notify({
        rowsDiff: diff,
        previousRowCount: countBefore,
        currentRowCount: this.rows.length,
        itemCount: this.items.length,
        rowCountChanged: countBefore !== this.rows.length,
        rowsChanged: diff.length > 0,
        dataView: this
      }, null, this);
    }
    /**
     * Wires the grid and the DataView together to keep row selection tied to item ids.
     * This is useful since, without it, the grid only knows about rows, so if the items
     * move around, the same rows stay selected instead of the selection moving along
     * with the items.
     *
     * NOTE:  This doesn't work with cell selection model.
     *
     * @param {SlickGrid} grid - The grid to sync selection with.
     * @param {Boolean} preserveHidden - Whether to keep selected items that go out of the
     *     view due to them getting filtered out.
     * @param {Boolean} [preserveHiddenOnSelectionChange] - Whether to keep selected items
     *     that are currently out of the view (see preserveHidden) as selected when selection
     *     changes.
     * @return {Event} An event that notifies when an internal list of selected row ids
     *     changes.  This is useful since, in combination with the above two options, it allows
     *     access to the full list selected row ids, and not just the ones visible to the grid.
     * @method syncGridSelection
     */
    syncGridSelection(grid, preserveHidden, preserveHiddenOnSelectionChange) {
      this._grid = grid;
      let inHandler;
      this.selectedRowIds = this.mapRowsToIds(grid.getSelectedRows());
      let setSelectedRowIds = (rowIds) => {
        rowIds === !1 ? this.selectedRowIds = [] : this.selectedRowIds.sort().join(",") !== rowIds.sort().join(",") && (this.selectedRowIds = rowIds);
      }, update = () => {
        if ((this.selectedRowIds || []).length > 0 && !inHandler) {
          inHandler = !0;
          let selectedRows = this.mapIdsToRows(this.selectedRowIds || []);
          if (!preserveHidden) {
            let selectedRowsChangedArgs = {
              grid: this._grid,
              ids: this.mapRowsToIds(selectedRows),
              rows: selectedRows,
              dataView: this
            };
            this.preSelectedRowIdsChangeFn(selectedRowsChangedArgs), this.onSelectedRowIdsChanged.notify(Object.assign(selectedRowsChangedArgs, {
              selectedRowIds: this.selectedRowIds,
              filteredIds: this.getAllSelectedFilteredIds()
            }), new SlickEventData(), this);
          }
          grid.setSelectedRows(selectedRows), inHandler = !1;
        }
      };
      return grid.onSelectedRowsChanged.subscribe((_e, args) => {
        if (!inHandler) {
          let newSelectedRowIds = this.mapRowsToIds(args.rows), selectedRowsChangedArgs = {
            grid: this._grid,
            ids: newSelectedRowIds,
            rows: args.rows,
            added: !0,
            dataView: this
          };
          this.preSelectedRowIdsChangeFn(selectedRowsChangedArgs), this.onSelectedRowIdsChanged.notify(Object.assign(selectedRowsChangedArgs, {
            selectedRowIds: this.selectedRowIds,
            filteredIds: this.getAllSelectedFilteredIds()
          }), new SlickEventData(), this);
        }
      }), this.preSelectedRowIdsChangeFn = (args) => {
        var _a2, _b2;
        if (!inHandler) {
          if (inHandler = !0, typeof args.added == "undefined")
            setSelectedRowIds(args.ids);
          else {
            let rowIds;
            if (args.added)
              preserveHiddenOnSelectionChange && grid.getOptions().multiSelect ? rowIds = ((_a2 = this.selectedRowIds) == null ? void 0 : _a2.filter((id) => this.getRowById(id) === void 0)).concat(args.ids) : rowIds = args.ids;
            else if (preserveHiddenOnSelectionChange && grid.getOptions().multiSelect) {
              let argsIdsSet = new Set(args.ids);
              rowIds = (_b2 = this.selectedRowIds) == null ? void 0 : _b2.filter((id) => !argsIdsSet.has(id));
            } else
              rowIds = [];
            setSelectedRowIds(rowIds);
          }
          inHandler = !1;
        }
      }, this.onRowsOrCountChanged.subscribe(update.bind(this)), this.onSelectedRowIdsChanged;
    }
    /**
     * Get all selected IDs
     * Note: when using Pagination it will also include hidden selections assuming `preserveHiddenOnSelectionChange` is set to true.
     */
    getAllSelectedIds() {
      return this.selectedRowIds;
    }
    /**
     * Get all selected filtered IDs (similar to "getAllSelectedIds" but only return filtered data)
     * Note: when using Pagination it will also include hidden selections assuming `preserveHiddenOnSelectionChange` is set to true.
     */
    getAllSelectedFilteredIds() {
      return this.getAllSelectedFilteredItems().map((item) => item[this.idProperty]);
    }
    /**
     * Set current row selected IDs array (regardless of Pagination)
     * NOTE: This will NOT change the selection in the grid, if you need to do that then you still need to call
     * "grid.setSelectedRows(rows)"
     * @param {Array} selectedIds - list of IDs which have been selected for this action
     * @param {Object} options
     *  - `isRowBeingAdded`: defaults to true, are the new selected IDs being added (or removed) as new row selections
     *  - `shouldTriggerEvent`: defaults to true, should we trigger `onSelectedRowIdsChanged` event
     *  - `applyRowSelectionToGrid`: defaults to true, should we apply the row selections to the grid in the UI
     */
    setSelectedIds(selectedIds, options) {
      var _a2;
      let isRowBeingAdded = options == null ? void 0 : options.isRowBeingAdded, shouldTriggerEvent = options == null ? void 0 : options.shouldTriggerEvent, applyRowSelectionToGrid = options == null ? void 0 : options.applyRowSelectionToGrid;
      isRowBeingAdded !== !1 && (isRowBeingAdded = !0);
      let selectedRows = this.mapIdsToRows(selectedIds), selectedRowsChangedArgs = {
        grid: this._grid,
        ids: selectedIds,
        rows: selectedRows,
        added: isRowBeingAdded,
        dataView: this
      };
      (_a2 = this.preSelectedRowIdsChangeFn) == null || _a2.call(this, selectedRowsChangedArgs), shouldTriggerEvent !== !1 && this.onSelectedRowIdsChanged.notify(Object.assign(selectedRowsChangedArgs, {
        selectedRowIds: this.selectedRowIds,
        filteredIds: this.getAllSelectedFilteredIds()
      }), new SlickEventData(), this), applyRowSelectionToGrid !== !1 && this._grid && this._grid.setSelectedRows(selectedRows);
    }
    /**
     * Get all selected dataContext items
     * Note: when using Pagination it will also include hidden selections assuming `preserveHiddenOnSelectionChange` is set to true.
     */
    getAllSelectedItems() {
      let selectedData = [];
      return this.getAllSelectedIds().forEach((id) => {
        selectedData.push(this.getItemById(id));
      }), selectedData;
    }
    /**
    * Get all selected filtered dataContext items (similar to "getAllSelectedItems" but only return filtered data)
    * Note: when using Pagination it will also include hidden selections assuming `preserveHiddenOnSelectionChange` is set to true.
    */
    getAllSelectedFilteredItems() {
      if (!Array.isArray(this.selectedRowIds))
        return [];
      let selectedRowIdSet = new Set(this.selectedRowIds);
      return this.filteredItems.filter((a) => selectedRowIdSet.has(a[this.idProperty])) || [];
    }
    syncGridCellCssStyles(grid, key) {
      let hashById, inHandler, storeCellCssStyles = (hash) => {
        hashById = {}, typeof hash == "object" && Object.keys(hash).forEach((row) => {
          if (hash) {
            let id = this.rows[row][this.idProperty];
            hashById[id] = hash[row];
          }
        });
      };
      storeCellCssStyles(grid.getCellCssStyles(key));
      let update = () => {
        if (typeof hashById == "object") {
          inHandler = !0, this.ensureRowsByIdCache();
          let newHash = {};
          Object.keys(hashById).forEach((id) => {
            var _a2;
            let row = (_a2 = this.rowsById) == null ? void 0 : _a2[id];
            Utils.isDefined(row) && (newHash[row] = hashById[id]);
          }), grid.setCellCssStyles(key, newHash), inHandler = !1;
        }
      };
      grid.onCellCssStylesChanged.subscribe((_e, args) => {
        inHandler || key === args.key && (args.hash ? storeCellCssStyles(args.hash) : (grid.onCellCssStylesChanged.unsubscribe(), this.onRowsOrCountChanged.unsubscribe(update)));
      }), this.onRowsOrCountChanged.subscribe(update.bind(this));
    }
  }, AvgAggregator = class {
    constructor(field) {
      __publicField(this, "_nonNullCount", 0);
      __publicField(this, "_sum", 0);
      __publicField(this, "_field");
      __publicField(this, "_type", "avg");
      this._field = field;
    }
    get field() {
      return this._field;
    }
    get type() {
      return this._type;
    }
    init() {
      this._nonNullCount = 0, this._sum = 0;
    }
    accumulate(item) {
      let val = item != null && item.hasOwnProperty(this._field) ? item[this._field] : null;
      val !== null && val !== "" && !isNaN(val) && (this._nonNullCount++, this._sum += parseFloat(val));
    }
    storeResult(groupTotals) {
      (!groupTotals || groupTotals[this._type] === void 0) && (groupTotals[this._type] = {}), this._nonNullCount !== 0 && (groupTotals[this._type][this._field] = this._sum / this._nonNullCount);
    }
  }, MinAggregator = class {
    constructor(field) {
      __publicField(this, "_min", null);
      __publicField(this, "_field");
      __publicField(this, "_type", "min");
      this._field = field;
    }
    get field() {
      return this._field;
    }
    get type() {
      return this._type;
    }
    init() {
      this._min = null;
    }
    accumulate(item) {
      let val = item != null && item.hasOwnProperty(this._field) ? item[this._field] : null;
      val !== null && val !== "" && !isNaN(val) && (this._min === null || val < this._min) && (this._min = parseFloat(val));
    }
    storeResult(groupTotals) {
      (!groupTotals || groupTotals[this._type] === void 0) && (groupTotals[this._type] = {}), groupTotals[this._type][this._field] = this._min;
    }
  }, MaxAggregator = class {
    constructor(field) {
      __publicField(this, "_max", null);
      __publicField(this, "_field");
      __publicField(this, "_type", "max");
      this._field = field;
    }
    get field() {
      return this._field;
    }
    get type() {
      return this._type;
    }
    init() {
      this._max = null;
    }
    accumulate(item) {
      let val = item != null && item.hasOwnProperty(this._field) ? item[this._field] : null;
      val !== null && val !== "" && !isNaN(val) && (this._max === null || val > this._max) && (this._max = parseFloat(val));
    }
    storeResult(groupTotals) {
      (!groupTotals || groupTotals[this._type] === void 0) && (groupTotals[this._type] = {}), groupTotals[this._type][this._field] = this._max;
    }
  }, SumAggregator = class {
    constructor(field) {
      __publicField(this, "_sum", 0);
      __publicField(this, "_field");
      __publicField(this, "_type", "sum");
      this._field = field;
    }
    get field() {
      return this._field;
    }
    get type() {
      return this._type;
    }
    init() {
      this._sum = 0;
    }
    accumulate(item) {
      let val = item != null && item.hasOwnProperty(this._field) ? item[this._field] : null;
      val !== null && val !== "" && !isNaN(val) && (this._sum += parseFloat(val));
    }
    storeResult(groupTotals) {
      (!groupTotals || groupTotals[this._type] === void 0) && (groupTotals[this._type] = {}), groupTotals[this._type][this._field] = this._sum;
    }
  }, CountAggregator = class {
    constructor(field) {
      __publicField(this, "_field");
      __publicField(this, "_type", "count");
      this._field = field;
    }
    get field() {
      return this._field;
    }
    get type() {
      return this._type;
    }
    init() {
    }
    storeResult(groupTotals) {
      (!groupTotals || groupTotals[this._type] === void 0) && (groupTotals[this._type] = {}), groupTotals[this._type][this._field] = groupTotals.group.rows.length;
    }
  }, Aggregators = {
    Avg: AvgAggregator,
    Min: MinAggregator,
    Max: MaxAggregator,
    Sum: SumAggregator,
    Count: CountAggregator
  };
  window.Slick && (window.Slick.Data = window.Slick.Data || {}, window.Slick.Data.DataView = SlickDataView, window.Slick.Data.Aggregators = Aggregators);
})();
//# sourceMappingURL=slick.dataview.js.map