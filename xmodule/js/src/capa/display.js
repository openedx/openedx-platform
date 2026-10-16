/* global MathJax, Collapsible, interpolate, JavascriptLoader, Logger, CodeMirror */
// Note: this code was originally converted from CoffeeScript, and thus follows some
// coding conventions that are discouraged by eslint. Some warnings have been suppressed
// to avoid substantial rewriting of the code. Allow the eslint suppressions to exceed
// the max line length of 120.
/* eslint max-len: ["error", 120, { "ignoreComments": true }] */

(function () {
  "use strict";

  var indexOfHelper =
    [].indexOf ||
    function (item) {
      var i, len;
      for (i = 0, len = this.length; i < len; i++) {
        if (i in this && this[i] === item) {
          return i;
        }
      }
      return -1;
    };

  /**
   * Check whether MathJax has been fully loaded and initialized (not just the config object).
   * In v3/v4, startup.promise is only set after the MathJax component loader completes.
   */
  var isMathJaxTypesetReady = function () {
    return typeof MathJax !== "undefined" && MathJax !== null &&
      typeof MathJax.startup !== "undefined" && MathJax.startup !== null &&
      typeof MathJax.startup.promise !== "undefined" &&
      typeof MathJax.typesetPromise === "function";
  };

  var isMathJaxRefreshReady = function () {
    return isMathJaxTypesetReady() &&
      typeof MathJax.typesetClear === "function" &&
      typeof MathJax.startup.document !== "undefined" && MathJax.startup.document !== null &&
      typeof MathJax.startup.document.getMathItemsWithin === "function";
  };

  var isMathJaxMathMLReady = function () {
    return typeof MathJax !== "undefined" && MathJax !== null &&
      typeof MathJax.startup !== "undefined" && MathJax.startup !== null &&
      typeof MathJax.startup.promise !== "undefined" &&
      typeof MathJax.startup.toMML === "function";
  };

  this.Problem = function () {
    function Problem(element) {
      var that = this;
      this.hint_button = function () {
        return Problem.prototype.hint_button.apply(that, arguments);
      };
      this.enableSubmitButtonAfterTimeout = function () {
        return Problem.prototype.enableSubmitButtonAfterTimeout.apply(that, arguments);
      };
      this.enableSubmitButtonAfterResponse = function () {
        return Problem.prototype.enableSubmitButtonAfterResponse.apply(that, arguments);
      };
      this.enableSubmitButton = function (enable, changeText) {
        if (changeText === null || changeText === undefined) {
          changeText = true; // eslint-disable-line no-param-reassign
        }
        return Problem.prototype.enableSubmitButton.apply(that, arguments);
      };
      this.disableAllButtonsWhileRunning = function (
        operationCallback,
        isFromCheckOperation, // eslint-disable-line no-unused-vars
      ) {
        return Problem.prototype.disableAllButtonsWhileRunning.apply(that, arguments);
      };
      this.submitAnswersAndSubmitButton = function (bind) {
        if (bind === null || bind === undefined) {
          bind = false; // eslint-disable-line no-param-reassign
        }
        return Problem.prototype.submitAnswersAndSubmitButton.apply(that, arguments);
      };
      this.refreshAnswers = function () {
        return Problem.prototype.refreshAnswers.apply(that, arguments);
      };
      this.updateMathML = function (jax, el) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.updateMathML.apply(that, arguments);
      };
      this.refreshMath = function (event, el) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.refreshMath.apply(that, arguments);
      };
      this.save_internal = function () {
        return Problem.prototype.save_internal.apply(that, arguments);
      };
      this.save = function () {
        return Problem.prototype.save.apply(that, arguments);
      };
      this.gentle_alert = function (msg) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.gentle_alert.apply(that, arguments);
      };
      this.clear_all_notifications = function () {
        return Problem.prototype.clear_all_notifications.apply(that, arguments);
      };
      this.show = function () {
        return Problem.prototype.show.apply(that, arguments);
      };
      this.reset_internal = function () {
        return Problem.prototype.reset_internal.apply(that, arguments);
      };
      this.reset = function () {
        return Problem.prototype.reset.apply(that, arguments);
      };
      this.get_sr_status = function (contents) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.get_sr_status.apply(that, arguments);
      };
      this.submit_internal = function () {
        return Problem.prototype.submit_internal.apply(that, arguments);
      };
      this.submit = function () {
        return Problem.prototype.submit.apply(that, arguments);
      };
      this.submit_fd = function () {
        return Problem.prototype.submit_fd.apply(that, arguments);
      };
      this.focus_on_save_notification = function () {
        return Problem.prototype.focus_on_save_notification.apply(that, arguments);
      };
      this.focus_on_hint_notification = function () {
        return Problem.prototype.focus_on_hint_notification.apply(that, arguments);
      };
      this.focus_on_submit_notification = function () {
        return Problem.prototype.focus_on_submit_notification.apply(that, arguments);
      };
      this.focus_on_notification = function (type) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.focus_on_notification.apply(that, arguments);
      };
      this.scroll_to_problem_meta = function () {
        return Problem.prototype.scroll_to_problem_meta.apply(that, arguments);
      };
      this.submit_save_waitfor = function (callback) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.submit_save_waitfor.apply(that, arguments);
      };
      this.setupInputTypes = function () {
        return Problem.prototype.setupInputTypes.apply(that, arguments);
      };
      this.poll = function (
        prevTimeout,
        focusCallback, // eslint-disable-line no-unused-vars
      ) {
        return Problem.prototype.poll.apply(that, arguments);
      };
      this.queueing = function (focusCallback) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.queueing.apply(that, arguments);
      };
      this.forceUpdate = function (response) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.forceUpdate.apply(that, arguments);
      };
      this.updateProgress = function (response) {
        // eslint-disable-line no-unused-vars
        return Problem.prototype.updateProgress.apply(that, arguments);
      };
      this.renderProgressState = function () {
        return Problem.prototype.renderProgressState.apply(that, arguments);
      };
      this.bind = function () {
        return Problem.prototype.bind.apply(that, arguments);
      };
      this.el = $(element).find(".problems-wrapper");
      this.id = this.el.data("problem-id");
      this.element_id = this.el.attr("id");
      this.url = this.el.data("url");
      this.content = this.el.data("content");

      // has_timed_out and has_response are used to ensure that
      // we wait a minimum of ~ 1s before transitioning the submit
      // button from disabled to enabled
      this.has_timed_out = false;
      this.has_response = false;
      this.render(this.content);
    }

    Problem.prototype.$ = function (selector) {
      return $(selector, this.el);
    };

    Problem.prototype.bind = function () {
      var problemPrefix,
        that = this;
      if (isMathJaxTypesetReady()) {
        this.el.find(".problem > div").each(function (index, element) {
          return MathJax.startup.promise.then(() => MathJax.typesetPromise([element]));
        });
      }
      if (window.hasOwnProperty("update_schematics")) {
        window.update_schematics();
      }
      problemPrefix = this.element_id.replace(/problem_/, "");
      this.inputs = this.$('[id^="input_' + problemPrefix + '_"]');
      this.$("div.action button").click(this.refreshAnswers);
      this.reviewButton = this.$(".notification-btn.review-btn");
      this.reviewButton.click(this.scroll_to_problem_meta);
      this.submitButton = this.$(".action .submit");
      this.submitButtonLabel = this.$(".action .submit .submit-label");
      this.submitButtonSubmitText = this.submitButtonLabel.text();
      this.submitButtonSubmittingText = this.submitButton.data("submitting");
      this.submitButton.click(this.submit_fd);
      this.hintButton = this.$(".action .hint-button");
      this.hintButton.click(this.hint_button);
      this.resetButton = this.$(".action .reset");
      this.resetButton.click(this.reset);
      this.showButton = this.$(".action .show");
      this.showButton.click(this.show);
      this.saveButton = this.$(".action .save");
      this.saveNotification = this.$(".notification-save");
      this.showAnswerNotification = this.$(".notification-show-answer");
      this.saveButton.click(this.save);
      this.gentleAlertNotification = this.$(".notification-gentle-alert");
      this.submitNotification = this.$(".notification-submit");

      // Accessibility helper for sighted keyboard users to show <clarification> tooltips on focus:
      this.$(".clarification").focus(function (ev) {
        var icon;
        icon = $(ev.target).children("i");
        return window.globalTooltipManager.openTooltip(icon);
      });
      this.$(".clarification").blur(function () {
        return window.globalTooltipManager.hide();
      });
      this.$(".review-btn").focus(function (ev) {
        return $(ev.target).removeClass("sr");
      });
      this.$(".review-btn").blur(function (ev) {
        return $(ev.target).addClass("sr");
      });
      this.bindResetCorrectness();
      if (this.submitButton.length) {
        this.submitAnswersAndSubmitButton(true);
      }
      Collapsible.setCollapsibles(this.el);
      this.$("input.math, .formulaequationinput input").keyup(this.refreshMath);
      if (isMathJaxTypesetReady()) {
        this.$("input.math, .formulaequationinput input").each(function (index, element) {
          return MathJax.startup.promise.then(() => that.refreshMath(null, element));
        });
      }
    };

    Problem.prototype.renderProgressState = function () {
      var graded, progress, progressTemplate, curScore, totalScore, attemptsUsed;
      curScore = this.el.data("problem-score");
      totalScore = this.el.data("problem-total-possible");
      attemptsUsed = this.el.data("attempts-used");
      graded = this.el.data("graded");

      // The problem is ungraded if it's explicitly marked as such, or if the total possible score is 0
      if (graded === "True" && totalScore !== 0) {
        graded = true;
      } else {
        graded = false;
      }

      if (curScore === undefined || totalScore === undefined) {
        // Render an empty string.
        progressTemplate = "";
      } else if (curScore === null || curScore === "None") {
        // Render 'x point(s) possible (un/graded, results hidden)' if no current score provided.
        if (graded) {
          progressTemplate = ngettext(
            // Translators: {num_points} is the number of points possible (examples: 1, 3, 10).;
            "{num_points} point possible (graded, results hidden)",
            "{num_points} points possible (graded, results hidden)",
            totalScore,
          );
        } else {
          progressTemplate = ngettext(
            // Translators: {num_points} is the number of points possible (examples: 1, 3, 10).;
            "{num_points} point possible (ungraded, results hidden)",
            "{num_points} points possible (ungraded, results hidden)",
            totalScore,
          );
        }
      } else if ((attemptsUsed === 0 || totalScore === 0) && curScore === 0) {
        // Render 'x point(s) possible' if student has not yet attempted question
        // But if staff has overridden score to a non-zero number, show it
        if (graded) {
          progressTemplate = ngettext(
            // Translators: {num_points} is the number of points possible (examples: 1, 3, 10).;
            "{num_points} point possible (graded)",
            "{num_points} points possible (graded)",
            totalScore,
          );
        } else {
          progressTemplate = ngettext(
            // Translators: {num_points} is the number of points possible (examples: 1, 3, 10).;
            "{num_points} point possible (ungraded)",
            "{num_points} points possible (ungraded)",
            totalScore,
          );
        }
      } else {
        // Render 'x/y point(s)' if student has attempted question
        if (graded) {
          progressTemplate = ngettext(
            // This comment needs to be on one line to be properly scraped for the translators.
            // Translators: {earned} is the number of points earned. {possible} is the total number of points (examples: 0/1, 1/1, 2/3, 5/10). The total number of points will always be at least 1. We pluralize based on the total number of points (example: 0/1 point; 1/2 points);
            "{earned}/{possible} point (graded)",
            "{earned}/{possible} points (graded)",
            totalScore,
          );
        } else {
          progressTemplate = ngettext(
            // This comment needs to be on one line to be properly scraped for the translators.
            // Translators: {earned} is the number of points earned. {possible} is the total number of points (examples: 0/1, 1/1, 2/3, 5/10). The total number of points will always be at least 1. We pluralize based on the total number of points (example: 0/1 point; 1/2 points);
            "{earned}/{possible} point (ungraded)",
            "{earned}/{possible} points (ungraded)",
            totalScore,
          );
        }
      }
      progress = edx.StringUtils.interpolate(progressTemplate, {
        earned: curScore,
        num_points: totalScore,
        possible: totalScore,
      });
      return this.$(".problem-progress").text(progress);
    };

    Problem.prototype.updateProgress = function (response) {
      if (response.progress_changed) {
        this.el.data("problem-score", this.convertToFloat(response.current_score));
        this.el.data("problem-total-possible", this.convertToFloat(response.total_possible));
        this.el.data("attempts-used", response.attempts_used);
        this.el.trigger("progressChanged");
      }
      return this.renderProgressState();
    };

    Problem.prototype.convertToFloat = function (num) {
      if (typeof num !== "number" || !Number.isInteger(num)) {
        return num;
      }
      return num.toFixed(1);
    };

    Problem.prototype.forceUpdate = function (response) {
      this.el.data("problem-score", response.current_score);
      this.el.data("problem-total-possible", response.total_possible);
      this.el.data("attempts-used", response.attempts_used);
      this.el.trigger("progressChanged");
      return this.renderProgressState();
    };

    Problem.prototype.queueing = function (focusCallback) {
      var that = this;
      this.queued_items = this.$(".xqueue");
      this.num_queued_items = this.queued_items.length;
      if (this.num_queued_items > 0) {
        if (window.queuePollerID) {
          // Only one poller 'thread' per Problem
          window.clearTimeout(window.queuePollerID);
        }
        window.queuePollerID = window.setTimeout(function () {
          return that.poll(1000, focusCallback);
        }, 1000);
      }
    };

    Problem.prototype.poll = function (previousTimeout, focusCallback) {
      var that = this;
      return $.postWithPrefix("" + this.url + "/problem_get", function (response) {
        var newTimeout;
        // If queueing status changed, then render
        that.new_queued_items = $(response.html).find(".xqueue");
        if (that.new_queued_items.length !== that.num_queued_items) {
          edx.HtmlUtils.setHtml(that.el, edx.HtmlUtils.HTML(response.html))
            .promise()
            .done(function () {
              // eslint-disable-next-line no-void
              return typeof focusCallback === "function" ? focusCallback() : void 0;
            });
          JavascriptLoader.executeModuleScripts(that.el, function () {
            that.setupInputTypes();
            that.bind();
          });
        }
        that.num_queued_items = that.new_queued_items.length;
        if (that.num_queued_items === 0) {
          that.forceUpdate(response);
          delete window.queuePollerID;
        } else {
          newTimeout = previousTimeout * 2;
          // if the timeout is greather than 1 minute
          if (newTimeout >= 60000) {
            delete window.queuePollerID;
            that.gentle_alert(gettext("The grading process is still running. Refresh the page to see updates."));
          } else {
            window.queuePollerID = window.setTimeout(function () {
              return that.poll(newTimeout, focusCallback);
            }, newTimeout);
          }
        }
      });
    };

    /**
     * Use this if you want to make an ajax call on the input type object
     * static method so you don't have to instantiate a Problem in order to use it
     *
     * Input:
     *     url: the AJAX url of the problem
     *     inputId: the inputId of the input you would like to make the call on
     *         NOTE: the id is the ${id} part of "input_${id}" during rendering
     *             If this function is passed the entire prefixed id, the backend may have trouble
     *             finding the correct input
     *     dispatch: string that indicates how this data should be handled by the inputtype
     *     data: dictionary of data to send to the server
     *     callback: the function that will be called once the AJAX call has been completed.
     *          It will be passed a response object
     */
    Problem.inputAjax = function (url, inputId, dispatch, data, callback) {
      data.dispatch = dispatch; // eslint-disable-line no-param-reassign
      data.input_id = inputId; // eslint-disable-line no-param-reassign
      return $.postWithPrefix("" + url + "/input_ajax", data, callback);
    };

    Problem.prototype.render = function (content, focusCallback) {
      var that = this;
      if (content) {
        edx.HtmlUtils.setHtml(this.el, edx.HtmlUtils.HTML(content));
        return JavascriptLoader.executeModuleScripts(this.el, function () {
          that.setupInputTypes();
          that.bind();
          that.queueing(focusCallback);
          that.renderProgressState();
          // eslint-disable-next-line no-void
          return typeof focusCallback === "function" ? focusCallback() : void 0;
        });
      } else {
        return $.postWithPrefix("" + this.url + "/problem_get", function (response) {
          edx.HtmlUtils.setHtml(that.el, edx.HtmlUtils.HTML(response.html));
          return JavascriptLoader.executeModuleScripts(that.el, function () {
            that.setupInputTypes();
            that.bind();
            that.queueing();
            return that.forceUpdate(response);
          });
        });
      }
    };

    Problem.prototype.setupInputTypes = function () {
      var that = this;
      this.inputtypeDisplays = {};
      return this.el.find(".capa_inputtype").each(function (index, inputtype) {
        var classes, cls, id, setupMethod, i, len, results;
        classes = $(inputtype).attr("class").split(" ");
        id = $(inputtype).attr("id");
        results = [];
        for (i = 0, len = classes.length; i < len; i++) {
          cls = classes[i];
          setupMethod = that.inputtypeSetupMethods[cls];
          if (setupMethod != null) {
            results.push((that.inputtypeDisplays[id] = setupMethod(inputtype)));
          } else {
            // eslint-disable-next-line no-void
            results.push(void 0);
          }
        }
        return results;
      });
    };

    /**
     * If some function wants to be called before sending the answer to the
     * server, give it a chance to do so.
     *
     * submit_save_waitfor allows the callee to send alerts if the user's input is
     * invalid. To do so, the callee must throw an exception named "WaitforException".
     * This and any other errors or exceptions that arise from the callee are rethrown
     * and abort the submission.
     *
     * In order to use this feature, add a 'data-waitfor' attribute to the input,
     * and specify the function to be called by the submit button before sending off @answers
     */
    Problem.prototype.submit_save_waitfor = function (callback) {
      var flag,
        inp,
        i,
        len,
        ref,
        that = this;
      flag = false;
      ref = this.inputs;
      for (i = 0, len = ref.length; i < len; i++) {
        inp = ref[i];
        if ($(inp).is("input[waitfor]")) {
          try {
            $(inp).data("waitfor")(function () {
              that.refreshAnswers();
              return callback();
            });
          } catch (e) {
            if (e.name === "Waitfor Exception") {
              alert(e.message); // eslint-disable-line no-alert
            } else {
              alert(
                // eslint-disable-line no-alert
                gettext("Could not grade your answer. The submission was aborted."),
              );
            }
            throw e;
          }
          flag = true;
        } else {
          flag = false;
        }
      }
      return flag;
    };

    // Scroll to problem metadata and next focus is problem input
    Problem.prototype.scroll_to_problem_meta = function () {
      var questionTitle;
      questionTitle = this.$(".problem-header");
      if (questionTitle.length > 0) {
        $("html, body").animate(
          {
            scrollTop: questionTitle.offset().top,
          },
          500,
        );
        questionTitle.focus();
      }
    };

    Problem.prototype.focus_on_notification = function (type) {
      var notification;
      notification = this.$(".notification-" + type);
      if (notification.length > 0) {
        notification.focus();
      }
    };

    Problem.prototype.focus_on_submit_notification = function () {
      this.focus_on_notification("submit");
    };

    Problem.prototype.focus_on_hint_notification = function (hintIndex) {
      this.$(".notification-hint .notification-message > ol > li.hint-index-" + hintIndex).focus();
    };

    Problem.prototype.focus_on_save_notification = function () {
      this.focus_on_notification("save");
    };

    /**
     * 'submit_fd' uses FormData to allow file submissions in the 'problem_check' dispatch,
     *      in addition to simple querystring-based answers
     *
     * NOTE: The dispatch 'problem_check' is being singled out for the use of FormData;
     *       maybe preferable to consolidate all dispatches to use FormData
     */
    Problem.prototype.submit_fd = function () {
      var abortSubmission,
        error,
        errorHtml,
        errors,
        fd,
        fileNotSelected,
        fileTooLarge,
        maxFileSize,
        requiredFilesNotSubmitted,
        settings,
        timeoutId,
        unallowedFileSubmitted,
        i,
        len,
        that = this;

      // If there are no file inputs in the problem, we can fall back on submit.
      if (this.el.find("input:file").length === 0) {
        this.submit();
        return;
      }
      this.enableSubmitButton(false);
      if (!window.FormData) {
        alert(
          gettext(
            "Submission aborted! Sorry, your browser does not support file uploads. If you can, please use Chrome or Safari which have been verified to support file uploads.",
          ),
        ); // eslint-disable-line max-len, no-alert
        this.enableSubmitButton(true);
        return;
      }
      timeoutId = this.enableSubmitButtonAfterTimeout();
      fd = new FormData();

      // Sanity checks on submission
      maxFileSize = 4 * 1000 * 1000;
      fileTooLarge = false;
      fileNotSelected = false;
      requiredFilesNotSubmitted = false;
      unallowedFileSubmitted = false;

      errors = [];
      this.inputs.each(function (index, element) {
        var allowedFiles, file, maxSize, requiredFiles, loopI, loopLen, ref;
        if (element.type === "file") {
          requiredFiles = $(element).data("required_files");
          allowedFiles = $(element).data("allowed_files");
          ref = element.files;
          for (loopI = 0, loopLen = ref.length; loopI < loopLen; loopI++) {
            file = ref[loopI];
            if (allowedFiles.length !== 0 && indexOfHelper.call(allowedFiles, file.name) < 0) {
              unallowedFileSubmitted = true;
              errors.push(
                edx.StringUtils.interpolate(gettext("You submitted {filename}; only {allowedFiles} are allowed."), {
                  filename: file.name,
                  allowedFiles: allowedFiles,
                }),
              );
            }
            if (indexOfHelper.call(requiredFiles, file.name) >= 0) {
              requiredFiles.splice(requiredFiles.indexOf(file.name), 1);
            }
            if (file.size > maxFileSize) {
              fileTooLarge = true;
              maxSize = maxFileSize / (1000 * 1000);
              errors.push(
                edx.StringUtils.interpolate(gettext("Your file {filename} is too large (max size: {maxSize}MB)."), {
                  filename: file.name,
                  maxSize: maxSize,
                }),
              );
            }
            fd.append(element.id, file); // xss-lint: disable=javascript-jquery-append
          }
          if (element.files.length === 0) {
            fileNotSelected = true;
            // In case we want to allow submissions with no file
            fd.append(element.id, ""); // xss-lint: disable=javascript-jquery-append
          }
          if (requiredFiles.length !== 0) {
            requiredFilesNotSubmitted = true;
            errors.push(
              edx.StringUtils.interpolate(gettext("You did not submit the required files: {requiredFiles}."), {
                requiredFiles: requiredFiles,
              }),
            );
          }
        } else {
          fd.append(element.id, element.value); // xss-lint: disable=javascript-jquery-append
        }
      });
      if (fileNotSelected) {
        errors.push(gettext("You did not select any files to submit."));
      }
      errorHtml = "";
      for (i = 0, len = errors.length; i < len; i++) {
        error = errors[i];
        errorHtml = edx.HtmlUtils.joinHtml(
          errorHtml,
          edx.HtmlUtils.interpolateHtml(edx.HtmlUtils.HTML("<li>{error}</li>"), { error: error }),
        );
      }
      errorHtml = edx.HtmlUtils.interpolateHtml(edx.HtmlUtils.HTML("<ul>{errors}</ul>"), { errors: errorHtml });
      this.gentle_alert(errorHtml.toString());
      abortSubmission = fileTooLarge || fileNotSelected || unallowedFileSubmitted || requiredFilesNotSubmitted;
      if (abortSubmission) {
        window.clearTimeout(timeoutId);
        this.enableSubmitButton(true);
      } else {
        settings = {
          type: "POST",
          data: fd,
          processData: false,
          contentType: false,
          complete: this.enableSubmitButtonAfterResponse,
          success: function (response) {
            switch (response.success) {
              case "submitted":
              case "incorrect":
              case "correct":
                that.render(response.contents);
                that.updateProgress(response);
                break;
              default:
                that.gentle_alert(response.success);
            }
            return Logger.log("problem_graded", [that.answers, response.contents], that.id);
          },
          error: function (response) {
            that.gentle_alert(response.responseJSON.success);
          },
        };
        $.ajaxWithPrefix("" + this.url + "/problem_check", settings);
      }
    };

    Problem.prototype.submit = function () {
      if (!this.submit_save_waitfor(this.submit_internal)) {
        this.disableAllButtonsWhileRunning(this.submit_internal, true);
      }
    };

    Problem.prototype.submit_internal = function () {
      var that = this;
      Logger.log("problem_check", this.answers);
      return $.postWithPrefix("" + this.url + "/problem_check", this.answers, function (response) {
        switch (response.success) {
          case "submitted":
          case "incorrect":
          case "correct":
            window.SR.readTexts(that.get_sr_status(response.contents));
            that.el.trigger("contentChanged", [that.id, response.contents, response]);
            that.render(response.contents, that.focus_on_submit_notification);
            that.updateProgress(response);
            // This is used by the Learning MFE to know when the Entrance Exam has been passed
            // for a user. The MFE is then able to respond appropriately.
            if (response.entrance_exam_passed) {
              window.parent.postMessage({ type: "entranceExam.passed" }, "*");
            }
            break;
          default:
            that.saveNotification.hide();
            that.gentle_alert(response.success);
        }
        return Logger.log("problem_graded", [that.answers, response.contents], that.id);
      });
    };

    /**
     * This method builds up an array of strings to send to the page screen-reader span.
     * It first gets all elements with class "status", and then looks to see if they are contained
     * in sections with aria-labels. If so, labels are prepended to the status element text.
     * If not, just the text of the status elements are returned.
     */
    Problem.prototype.get_sr_status = function (contents) {
      var addedStatus, ariaLabel, element, labeledStatus, parentSection, statusElement, template, i, len;
      statusElement = $(contents).find(".status");
      labeledStatus = [];
      for (i = 0, len = statusElement.length; i < len; i++) {
        element = statusElement[i];
        parentSection = $(element).closest(".wrapper-problem-response");
        addedStatus = false;
        if (parentSection) {
          ariaLabel = parentSection.attr("aria-label");
          if (ariaLabel) {
            // Translators: This is only translated to allow for reordering of label and associated status.;
            template = gettext("{label}: {status}");
            labeledStatus.push(
              edx.StringUtils.interpolate(template, {
                label: ariaLabel,
                status: $(element).text(),
              }),
            );
            addedStatus = true;
          }
        }
        if (!addedStatus) {
          labeledStatus.push($(element).text());
        }
      }
      return labeledStatus;
    };

    Problem.prototype.reset = function () {
      return this.disableAllButtonsWhileRunning(this.reset_internal, false);
    };

    Problem.prototype.reset_internal = function () {
      var that = this;
      Logger.log("problem_reset", this.answers);
      return $.postWithPrefix(
        "" + this.url + "/problem_reset",
        {
          id: this.id,
        },
        function (response) {
          if (response.success) {
            that.el.trigger("contentChanged", [that.id, response.html, response]);
            that.render(response.html, that.scroll_to_problem_meta);
            that.updateProgress(response);
            return window.SR.readText(gettext("This problem has been reset."));
          } else {
            return that.gentle_alert(response.msg);
          }
        },
      );
    };

    // TODO this needs modification to deal with javascript responses; perhaps we
    // need something where responsetypes can define their own behavior when show
    // is called.
    Problem.prototype.show = function () {
      var that = this;
      Logger.log("problem_show", {
        problem: this.id,
      });
      return $.postWithPrefix("" + this.url + "/problem_show", function (response) {
        var answers;
        answers = response.answers;
        $.each(answers, function (key, value) {
          var safeKey = key.replace(/\\/g, "\\\\").replace(/:/g, "\\:").replace(/\./g, "\\."); // fix for courses which use url_names with colons & periods, e.g. problem:question1, question1.1
          var answer;
          if (!$.isArray(value)) {
            answer = that.$("#answer_" + safeKey + ", #solution_" + safeKey);
            edx.HtmlUtils.setHtml(answer, edx.HtmlUtils.HTML(value));
            Collapsible.setCollapsibles(answer);

            // Sometimes, `value` is just a string containing a MathJax formula.
            // If this is the case, jQuery will throw an error in some corner cases
            // because of an incorrect selector. We setup a try..catch so that
            // the script doesn't break in such cases.
            //
            // We will fallback to the second `if statement` below, if an
            // error is thrown by jQuery.
            try {
              return $(value).find(".detailed-solution");
            } catch (e) {
              return {};
            }

            // TODO remove the above once everything is extracted into its own
            // inputtype functions.
          }
        });
        that.el.find(".capa_inputtype").each(function (index, inputtype) {
          var classes, cls, display, showMethod, i, len, results;
          classes = $(inputtype).attr("class").split(" ");
          results = [];
          for (i = 0, len = classes.length; i < len; i++) {
            cls = classes[i];
            display = that.inputtypeDisplays[$(inputtype).attr("id")];
            showMethod = that.inputtypeShowAnswerMethods[cls];
            if (showMethod != null) {
              results.push(showMethod(inputtype, display, answers, response.correct_status_html));
            } else {
              // eslint-disable-next-line no-void
              results.push(void 0);
            }
          }
          return results;
        });
        if (isMathJaxTypesetReady()) {
          that.el.find(".problem > div").each(function (index, element) {
            return MathJax.startup.promise.then(() => MathJax.typesetPromise([element]));
          });
        }
        that.el.find(".show").attr("disabled", "disabled");
        that.updateProgress(response);
        that.clear_all_notifications();
        that.showAnswerNotification.show();
        that.focus_on_notification("show-answer");
      });
    };

    Problem.prototype.clear_all_notifications = function () {
      this.submitNotification.remove();
      this.gentleAlertNotification.hide();
      this.saveNotification.hide();
      this.showAnswerNotification.hide();
    };

    Problem.prototype.gentle_alert = function (msg) {
      edx.HtmlUtils.setHtml(this.el.find(".notification-gentle-alert .notification-message"), edx.HtmlUtils.HTML(msg));
      this.clear_all_notifications();
      this.gentleAlertNotification.show();
      this.gentleAlertNotification.focus();
    };

    Problem.prototype.save = function () {
      if (!this.submit_save_waitfor(this.save_internal)) {
        this.disableAllButtonsWhileRunning(this.save_internal, false);
      }
    };

    Problem.prototype.save_internal = function () {
      var that = this;
      Logger.log("problem_save", this.answers);
      return $.postWithPrefix("" + this.url + "/problem_save", this.answers, function (response) {
        var saveMessage;
        saveMessage = response.msg;
        if (response.success) {
          that.el.trigger("contentChanged", [that.id, response.html, response]);
          edx.HtmlUtils.setHtml(
            that.el.find(".notification-save .notification-message"),
            edx.HtmlUtils.HTML(saveMessage),
          );
          that.clear_all_notifications();
          that.el.find(".wrapper-problem-response .message").hide();
          that.saveNotification.show();
          that.focus_on_save_notification();
        } else {
          that.gentle_alert(saveMessage);
        }
      });
    };

    Problem.prototype.refreshMath = function (event, element) {
      var elid,
          eqn,
          jax,
          mathjaxPreprocessor,
          preprocessorTag,
          isTexDelimited;
      if (!element) {
        element = event.target; // eslint-disable-line no-param-reassign
      }
      elid = element.id.replace(/^input_/, "");
      // MathJax preprocessor is loaded by 'setupInputTypes'
      preprocessorTag = "inputtype_" + elid;
      mathjaxPreprocessor = this.inputtypeDisplays[preprocessorTag];
      if (isMathJaxRefreshReady()) {
        var math = document.getElementById("display_" + elid);
        if (!math) {
          math = document.getElementById(element.id + "_preview");
        }
        if (!math) {
          return;
        }
        eqn = $(element).val();
        if (mathjaxPreprocessor) {
          eqn = mathjaxPreprocessor(eqn);
        }
        MathJax.typesetClear([math]);
        if (!eqn) {
          math.textContent = "";
          var dynInput = document.getElementById(element.id + "_dynamath");
          if (dynInput) { dynInput.value = ""; }
          return;
        }
        // Determine rendering mode:
        //   isTexDelimited  -> preserve user-written TeX delimiters as-is
        //   else            -> calculator/AsciiMath syntax via backtick delimiters
        isTexDelimited = (
          (/^\s*\\\(/.test(eqn) && /\\\)\s*$/.test(eqn)) ||
          (/^\s*\\\[/.test(eqn) && /\\\]\s*$/.test(eqn)) ||
          (/^\s*\$\$/.test(eqn) && /\$\$\s*$/.test(eqn)) ||
          (/^\s*\$[^$]/.test(eqn) && /[^$]\$\s*$/.test(eqn))
        );
        if (isTexDelimited) {
          math.textContent = eqn;
        } else {
          // Calculator/AsciiMath syntax: backtick-delimited for AsciiMath input jax
          math.textContent = "`" + eqn + "`";
        }
        MathJax.typesetPromise([math]).then(function () {
          jax = MathJax.startup.document.getMathItemsWithin(math)[0];
          if (jax) {
            this.updateMathML(jax, element);
          }
        }.bind(this)).catch(function () {
          var dynInput = document.getElementById(element.id + "_dynamath");
          if (dynInput) { dynInput.value = ""; }
        });
      }
    };

    Problem.prototype.updateMathML = function (jax, element) {
      if (isMathJaxMathMLReady()) {
        try {
          var dynInput = document.getElementById(element.id + "_dynamath");
          if (dynInput) { dynInput.value = MathJax.startup.toMML(jax.root); }
        } catch (exception) {
          if (!exception.restart) {
            throw exception;
          }
          MathJax.startup.promise
            .then(function () {
              this.refreshMath(null, element);
            }.bind(this));
        }
      }
    };

    Problem.prototype.refreshAnswers = function () {
      this.$("input.schematic").each(function (index, element) {
        return element.schematic.update_value();
      });
      this.$(".CodeMirror").each(function (index, element) {
        if (element.CodeMirror.save) {
          element.CodeMirror.save();
        }
      });
      this.answers = this.inputs.serialize();
    };

    /**
     * Used to check available answers and if something is checked (or the answer is set in some textbox),
     * the "Submit" button becomes enabled. Otherwise it is disabled by default.
     *
     * Arguments:
     *    bind (boolean): used on the first check to attach event handlers to input fields
     *       to change "Submit" enable status in case of some manipulations with answers
     */
    Problem.prototype.submitAnswersAndSubmitButton = function (bind) {
      var answered,
        atLeastOneTextInputFound,
        oneTextInputFilled,
        that = this;
      if (bind === null || bind === undefined) {
        bind = false; // eslint-disable-line no-param-reassign
      }
      answered = true;
      atLeastOneTextInputFound = false;
      oneTextInputFilled = false;
      this.el.find("input:text").each(function (i, textField) {
        if ($(textField).is(":visible")) {
          atLeastOneTextInputFound = true;
          if ($(textField).val() !== "") {
            oneTextInputFilled = true;
          }
          if (bind) {
            $(textField).on("input", function () {
              that.saveNotification.hide();
              that.showAnswerNotification.hide();
              that.submitAnswersAndSubmitButton();
            });
          }
        }
      });
      if (atLeastOneTextInputFound && !oneTextInputFilled) {
        answered = false;
      }
      this.el.find(".choicegroup").each(function (i, choicegroupBlock) {
        var checked;
        checked = false;
        $(choicegroupBlock)
          .find("input[type=checkbox], input[type=radio]")
          .each(function (j, checkboxOrRadio) {
            if ($(checkboxOrRadio).is(":checked")) {
              checked = true;
            }
            if (bind) {
              $(checkboxOrRadio).on("click", function () {
                that.saveNotification.hide();
                that.el.find(".show").removeAttr("disabled");
                that.showAnswerNotification.hide();
                that.submitAnswersAndSubmitButton();
              });
            }
          });
        if (!checked) {
          answered = false;
        }
      });
      this.el.find("select").each(function (i, selectField) {
        var selectedOption = $(selectField).find("option:selected").text().trim();
        if (selectedOption === "Select an option") {
          answered = false;
        }
        if (bind) {
          $(selectField).on("change", function () {
            that.saveNotification.hide();
            that.showAnswerNotification.hide();
            that.submitAnswersAndSubmitButton();
          });
        }
      });
      if (answered) {
        return this.enableSubmitButton(true);
      } else {
        return this.enableSubmitButton(false, false);
      }
    };

    Problem.prototype.bindResetCorrectness = function () {
      // Loop through all input types.
      // Bind the reset functions at that scope.
      var $inputtypes,
        that = this;
      $inputtypes = this.el.find(".capa_inputtype").add(this.el.find(".inputtype"));
      return $inputtypes.each(function (index, inputtype) {
        var bindMethod, classes, cls, i, len, results;
        classes = $(inputtype).attr("class").split(" ");
        results = [];
        for (i = 0, len = classes.length; i < len; i++) {
          cls = classes[i];
          bindMethod = that.bindResetCorrectnessByInputtype[cls];
          if (bindMethod != null) {
            results.push(bindMethod(inputtype));
          } else {
            // eslint-disable-next-line no-void
            results.push(void 0);
          }
        }
        return results;
      });
    };

    // Find all places where each input type displays its correct-ness
    // Replace them with their original state--'unanswered'.
    Problem.prototype.bindResetCorrectnessByInputtype = {
      // These are run at the scope of the capa inputtype
      // They should set handlers on each <input> to reset the whole.
      formulaequationinput: function (element) {
        return $(element)
          .find("input")
          .on("input", function () {
            var $p;
            $p = $(element).find("span.status");
            $p.removeClass("correct incorrect submitted");
            return $p.parent().removeAttr("class").addClass("unsubmitted");
          });
      },
      choicegroup: function (element) {
        var $element, id;
        $element = $(element);
        id = $element.attr("id").match(/^inputtype_(.*)$/)[1];
        return $element.find("input").on("change", function () {
          var $status;
          $status = $("#status_" + id);
          if ($status[0]) {
            $status.removeAttr("class").addClass("status unanswered");
          } else {
            $("<span>", {
              class: "status unanswered",
              style: "display: inline-block;",
              id: "status_" + id,
            });
          }
          $element.find("label").find("span.status.correct").remove();
          return $element.find("label").removeAttr("class");
        });
      },
      "option-input": function (element) {
        var $select, id;
        $select = $(element).find("select");
        id = $select.attr("id").match(/^input_(.*)$/)[1];
        return $select.on("change", function () {
          return $("#status_" + id)
            .removeAttr("class")
            .addClass("unanswered")
            .find(".sr")
            .text(gettext("unsubmitted"));
        });
      },
      textline: function (element) {
        return $(element)
          .find("input")
          .on("input", function () {
            var $p;
            $p = $(element).find("span.status");
            $p.removeClass("correct incorrect submitted");
            return $p.parent().removeClass("correct incorrect").addClass("unsubmitted");
          });
      },
    };

    Problem.prototype.inputtypeSetupMethods = {
      "text-input-dynamath": function (element) {
        /*
                 Return: function (eqn) -> eqn that preprocesses the user formula input before
                 it is fed into MathJax. Return 'false' if no preprocessor specified
                 */
        var data, preprocessor, preprocessorClass, preprocessorClassName;
        data = $(element).find(".text-input-dynamath_data");
        preprocessorClassName = data.data("preprocessor");
        preprocessorClass = window[preprocessorClassName];
        if (preprocessorClass == null) {
          return false;
        } else {
          preprocessor = new preprocessorClass();
          return preprocessor.fn;
        }
      },
      cminput: function (container) {
        var CodeMirrorEditor, CodeMirrorTextArea, element, id, linenumbers, mode, spaces, tabsize;
        element = $(container).find("textarea");
        tabsize = element.data("tabsize");
        mode = element.data("mode");
        linenumbers = element.data("linenums");
        spaces = Array(parseInt(tabsize, 10) + 1).join(" ");
        CodeMirrorEditor = CodeMirror.fromTextArea(element[0], {
          lineNumbers: linenumbers,
          indentUnit: tabsize,
          tabSize: tabsize,
          mode: mode,
          matchBrackets: true,
          lineWrapping: true,
          indentWithTabs: false,
          smartIndent: false,
          extraKeys: {
            Esc: function () {
              $(".grader-status").focus();
              return false;
            },
            Tab: function (cm) {
              cm.replaceSelection(spaces, "end");
              return false;
            },
          },
        });
        id = element.attr("id").replace(/^input_/, "");
        CodeMirrorTextArea = CodeMirrorEditor.getInputField();
        CodeMirrorTextArea.setAttribute("id", "cm-textarea-" + id);
        CodeMirrorTextArea.setAttribute("aria-describedby", "cm-editor-exit-message-" + id + " status_" + id);
        return CodeMirrorEditor;
      },
    };

    Problem.prototype.inputtypeShowAnswerMethods = {
      choicegroup: function (element, display, answers, correctStatusHtml) {
        var answer, choice, inputId, i, len, results, $element, $inputLabel, $inputStatus;
        $element = $(element);
        inputId = $element.attr("id").replace(/inputtype_/, "");
        var safeId = inputId.replace(/\\/g, "\\\\").replace(/:/g, "\\:").replace(/\./g, "\\."); // fix for courses which use url_names with colons & periods, e.g. problem:question1, question1.1
        answer = answers[inputId];
        results = [];
        for (i = 0, len = answer.length; i < len; i++) {
          choice = answer[i];
          $inputLabel = $element.find("#input_" + safeId + "_" + choice + " + label");
          $inputStatus = $element.find("#status_" + safeId);
          // If the correct answer was already Submitted before "Show Answer" was selected,
          // the status HTML will already be present. Otherwise, inject the status HTML.

          // If the learner clicked a different answer after Submit, their submitted answers
          // will be marked as "unanswered". In that case, for correct answers update the
          // classes accordingly.
          if ($inputStatus.hasClass("unanswered")) {
            edx.HtmlUtils.append($inputLabel, edx.HtmlUtils.HTML(correctStatusHtml));
            $inputLabel.addClass("choicegroup_correct");
          } else if (!$inputLabel.hasClass("choicegroup_correct")) {
            // If the status HTML is not already present (due to clicking Submit), append
            // the status HTML for correct answers.
            edx.HtmlUtils.append($inputLabel, edx.HtmlUtils.HTML(correctStatusHtml));
            $inputLabel.removeClass("choicegroup_incorrect");
            results.push($inputLabel.addClass("choicegroup_correct"));
          }
        }
        return results;
      },
      choicetextgroup: function (element, display, answers) {
        var answer, choice, inputId, i, len, results, $element;
        $element = $(element);
        inputId = $element.attr("id").replace(/inputtype_/, "");
        answer = answers[inputId];
        results = [];
        for (i = 0, len = answer.length; i < len; i++) {
          choice = answer[i];
          results.push($element.find("section#forinput" + choice).addClass("choicetextgroup_show_correct"));
        }
        return results;
      },
      imageinput: function (element, display, answers) {
        // answers is a dict of (answer_id, answer_text) for each answer for this question.
        //
        // @Examples:
        // {'anwser_id': {
        //    'rectangle': '(10,10)-(20,30);(12,12)-(40,60)',
        //    'regions': '[[10,10], [30,30], [10, 30], [30, 10]]'
        // } }
        var canvas, container, id, types, context, $element;
        types = {
          rectangle: function (ctx, coords) {
            var rects, reg;
            reg = /^\(([0-9]+),([0-9]+)\)-\(([0-9]+),([0-9]+)\)$/;
            rects = coords.replace(/\s*/g, "").split(/;/);
            $.each(rects, function (index, rect) {
              var abs, height, points, width;
              abs = Math.abs;
              points = reg.exec(rect);
              if (points) {
                width = abs(points[3] - points[1]);
                height = abs(points[4] - points[2]);
                ctx.rect(points[1], points[2], width, height);
              }
            });
            ctx.stroke();
            return ctx.fill();
          },
          regions: function (ctx, coords) {
            var parseCoords;
            parseCoords = function (coordinates) {
              var reg;
              reg = JSON.parse(coordinates);

              // Regions is list of lists [region1, region2, region3, ...] where regionN
              // is disordered list of points: [[1,1], [100,100], [50,50], [20, 70]].
              // If there is only one region in the list, simpler notation can be used:
              // regions="[[10,10], [30,30], [10, 30], [30, 10]]" (without explicitly
              // setting outer list)
              if (typeof reg[0][0][0] === "undefined") {
                // we have [[1,2],[3,4],[5,6]] - single region
                // instead of [[[1,2],[3,4],[5,6], [[1,2],[3,4],[5,6]]]
                // or [[[1,2],[3,4],[5,6]]] - multiple regions syntax
                reg = [reg];
              }
              return reg;
            };
            return $.each(parseCoords(coords), function (index, region) {
              ctx.beginPath();
              $.each(region, function (idx, point) {
                if (idx === 0) {
                  return ctx.moveTo(point[0], point[1]);
                } else {
                  return ctx.lineTo(point[0], point[1]);
                }
              });
              ctx.closePath();
              ctx.stroke();
              return ctx.fill();
            });
          },
        };
        $element = $(element);
        id = $element.attr("id").replace(/inputtype_/, "");
        container = $element.find("#answer_" + id);
        canvas = document.createElement("canvas");
        canvas.width = container.data("width");
        canvas.height = container.data("height");
        if (canvas.getContext) {
          context = canvas.getContext("2d");
        } else {
          console.log("Canvas is not supported."); // eslint-disable-line no-console
        }
        context.fillStyle = "rgba(255,255,255,.3)";
        context.strokeStyle = "#FF0000";
        context.lineWidth = "2";
        if (answers[id]) {
          $.each(answers[id], function (key, value) {
            if (types[key] !== null && types[key] !== undefined && value) {
              types[key](context, value);
            }
          });
          edx.HtmlUtils.setHtml(container, edx.HtmlUtils.HTML(canvas));
        } else {
          console.log("Answer is absent for image input with id=" + id); // eslint-disable-line no-console
        }
      },
    };

    /**
     * Used to keep the buttons disabled while operationCallback is running.
     *
     * params:
     *      'operationCallback' is an operation to be run.
     *      isFromCheckOperation' is a boolean to keep track if 'operationCallback' was
     *           from submit, if so then text of submit button will be changed as well.
     *
     */
    Problem.prototype.disableAllButtonsWhileRunning = function (operationCallback, isFromCheckOperation) {
      var that = this;
      var allButtons = [this.resetButton, this.saveButton, this.showButton, this.hintButton, this.submitButton];
      var initiallyEnabledButtons = allButtons.filter(function (button) {
        return !button.attr("disabled");
      });
      this.enableButtons(initiallyEnabledButtons, false, isFromCheckOperation);
      return operationCallback().always(function () {
        return that.enableButtons(initiallyEnabledButtons, true, isFromCheckOperation);
      });
    };

    /**
     * Enables/disables buttons by removing/adding the disabled attribute. The submit button is checked
     *     separately due to the changing text it contains.
     *
     * params:
     *     'buttons' is an array of buttons that will have their 'disabled' attribute modified
     *     'enable' a boolean to either enable or disable the buttons passed in the first parameter
     *     'changeSubmitButtonText' is a boolean to keep track if operation was initiated
     *         from submit so that text of submit button will also be changed while disabling/enabling
     *         the submit button.
     */
    Problem.prototype.enableButtons = function (buttons, enable, changeSubmitButtonText) {
      var that = this;
      buttons.forEach(function (button) {
        if (button.hasClass("submit")) {
          that.enableSubmitButton(enable, changeSubmitButtonText);
        } else if (enable) {
          button.removeAttr("disabled");
        } else {
          button.attr({ disabled: "disabled" });
        }
      });
    };

    /**
     *  Used to disable submit button to reduce chance of accidental double-submissions.
     *
     * params:
     *     'enable' is a boolean to determine enabling/disabling of submit button.
     *     'changeText' is a boolean to determine if there is need to change the
     *         text of submit button as well.
     */
    Problem.prototype.enableSubmitButton = function (enable, changeText) {
      var submitCanBeEnabled;
      if (changeText === null || changeText === undefined) {
        changeText = true; // eslint-disable-line no-param-reassign
      }
      if (enable) {
        submitCanBeEnabled = this.submitButton.data("should-enable-submit-button") === "True";
        if (submitCanBeEnabled) {
          this.submitButton.removeAttr("disabled");
        }
        if (changeText) {
          this.submitButtonLabel.text(this.submitButtonSubmitText);
        }
      } else {
        this.submitButton.attr({ disabled: "disabled" });
        if (changeText) {
          this.submitButtonLabel.text(this.submitButtonSubmittingText);
        }
      }
    };

    Problem.prototype.enableSubmitButtonAfterResponse = function () {
      this.has_response = true;
      if (!this.has_timed_out) {
        // Server has returned response before our timeout.
        return this.enableSubmitButton(false);
      } else {
        return this.enableSubmitButton(true);
      }
    };

    Problem.prototype.enableSubmitButtonAfterTimeout = function () {
      var enableSubmitButton,
        that = this;
      this.has_timed_out = false;
      this.has_response = false;
      enableSubmitButton = function () {
        that.has_timed_out = true;
        if (that.has_response) {
          that.enableSubmitButton(true);
        }
      };
      return window.setTimeout(enableSubmitButton, 750);
    };

    Problem.prototype.hint_button = function () {
      // Store the index of the currently shown hint as an attribute.
      // Use that to compute the next hint number when the button is clicked.
      var hintContainer,
        hintIndex,
        nextIndex,
        that = this;
      hintContainer = this.$(".problem-hint");
      hintIndex = hintContainer.attr("hint_index");
      // eslint-disable-next-line no-void
      if (hintIndex === void 0) {
        nextIndex = 0;
      } else {
        nextIndex = parseInt(hintIndex, 10) + 1;
      }
      return $.postWithPrefix(
        "" + this.url + "/hint_button",
        {
          hint_index: nextIndex,
          input_id: this.id,
        },
        function (response) {
          var hintMsgContainer;
          if (response.success) {
            hintMsgContainer = that.$(".problem-hint .notification-message");
            hintContainer.attr("hint_index", response.hint_index);
            edx.HtmlUtils.setHtml(hintMsgContainer, edx.HtmlUtils.HTML(response.msg));
            if (isMathJaxTypesetReady()) {
              MathJax.startup.promise
                .then(() => MathJax.typesetPromise([hintContainer[0]]));
            }
            if (response.should_enable_next_hint) {
              that.hintButton.removeAttr("disabled");
            } else {
              that.hintButton.attr({ disabled: "disabled" });
            }
            that.el.find(".notification-hint").show();
            that.focus_on_hint_notification(nextIndex);
          } else {
            that.gentle_alert(response.msg);
          }
        },
      );
    };

    return Problem;
  }.call(this);
}).call(this);
