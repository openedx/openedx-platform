1. Offline content generation for mobile OeX app
=============================================

Status
------

Accepted

Context
-------

The primary goal is to enable offline access to course content in the Open edX mobile application.
This will allow users to download course materials when they have internet access and access them
later without an internet connection, also it should support synchronization of the submitted results
with backend service as connection become available again. This feature is crucial for learners
in areas with unreliable internet connectivity or those who prefer to study on the go without using mobile data.
It is possible to provide different kind of content using the Open edX platform, such as read-only materials,
videos, and assessments. Therefore to provide the whole course experience in offline mode it's required to
make all these types of content available offline. Of course it won't be feasible to recreate grading
algorithms in mobile, so it's possible to save submission on the mobile app and execute synchronization
of the user progres as not limited connectivity is back.

From the product perspective the following Figma designs and product requirements should be considered:

* `Download and Delete (Figma)`_
* `Downloads (Figma)`_

.. _Download and Delete (Figma): https://www.figma.com/design/iZ56YMjbRMShCCDxqrqRrR/Mobile-App-v2.4-%5BOpen-edX%5D?node-id=18472-187387&t=tMgymS6WIZZJbJHn-0
.. _Downloads (Figma): https://www.figma.com/design/iZ56YMjbRMShCCDxqrqRrR/Mobile-App-v2.4-%5BOpen-edX%5D

Decision
--------

The implementation of the offline content support require addition of the following features to the edx-platform:

* It's necessary to generate an archive with all necessary HTML and assets for a student view of an xBlock, so it's possible to display an xBlock using mobile WebView.
* Implement a new standard XBlock view called `offline_view` which would generate user-agnostic fragments suitable for offline use. This view will avoid any dependence on student-specific state, focusing solely on content and settings.
* XBlock classes can opt into supporting `offline_view`. They can implement this view fully or partially. For example, a block that relies on user-specific randomization or interactive elements that require online connectivity would not be rendered offline.
* The generated offline content should be provided to mobile device through mobile API.
* To support CAPA problems and other kinds of assessments in offline mode it's necessary to create an additional
  JavaScript layer that will allow communication with Mobile applications by sending JSON messages
  using Android and IOS Bridge.


Offline content generation
~~~~~~~~~~~~~~~~~~~~~~~~~~

Generating zip archive with xBlock data for HTML and CAPA problems
When content is published in CMS and offline generation is enabled for the course or entire platform using waffle flags, the content generation task should be started for supported blocks.
Every time block content republished ZIP archive with offline content should be regenerated.
Supported XBlock class should implement `offline_view` method that will be used to generate the content.
HTML should be processed, all related assets files, images and scripts should be included in the generated ZIP archive with offline content
The Generation process should work with local media storage as well as s3.
If error retrieving block happened, the generation task will be scheduled for retry 2 more times, with progressive delay.

    .. image:: _images/mobile_offline_content_generation.svg
        :alt: Mobile Offline Content Generation Process Diagram


Offline content deletion
~~~~~~~~~~~~~~~~~~~~~~~~

When the course is published and some blocks are removed from the course, related ZIP archive should be deleted.
When some blocks are removed from the course without publishing the course, the related ZIP archive shouldn't be deleted.


Mobile API extension
~~~~~~~~~~~~~~~~~~~~

Extend the Course Home mobile API endpoint, and add a new version of the API (url /api/mobile/v4/course_info/blocks/)
to return information about offline content available for download for supported blocks

.. code-block:: json
    {
        ...
        "offline_download": {
            "file_url": "{file_url}" or null,
            "last_modified": "{DT}" or null,
            "file_size": ""
          }
    }


JavaScript Bridge for interaction with mobile applications
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Implement JS Bridge JS script to intercept and send results to mobile device for supported CAPA problems.

The JS bridge will intercept AJAX requests in the mobile application and store the responses locally. If the user submits the response offline he will be shown the message "Your response is accepted" and the Submit button will be disabled as the submission will be sent twice.
When the internet connection is back the mobile client will submit the cached responses one by one through the regular xBlock handler endpoints.
Data from submission should be submitted through bridge on iOS and Android devices.
This script should expose markCompleted JS function so mobile can change state of the offline problem after the data was saved into internal database or on initialization of the problem.

* **Implement of a mechanism for generating and storing on a server or external storage**: The course content should be pre-generated and saved to the storage for later download.
    * **Render block fragment**: Implement a new standard XBlock view called `offline_view` which would generate user-agnostic fragments suitable for offline use. This view will avoid any dependence on student-specific state, focusing solely on content and settings.
    * **Replace static and media**: Save static and media assets files used in block to temporary directory and replace their static paths with local paths.
    * **Archive and store content**: Archive the generated content and store it on the server or external storage.
* **Mechanism for updating the generated data**: When updating course blocks (namely when publishing) the content that has been changed should be re-generated.
    * **Track course publishing events on CMS side**: Add a new signal `course_cache_updated` to be called after the course structure cache update in `update_course_in_cache_v2`. Add a signal that listens to `course_cache_updated` and starts block generation.
    * **Update archive**: Check generated archive creation date and update it if less than course publishing date.
* **Implement a Mobile Local Storage Mechanism**: Use the device's local storage to save course content for offline access.
    * **Extend blocks API**: Add links to download blocks content and  where it is possible.
* **Sync Mechanism**: Periodically synchronize local data with the server when the device is online.
    * **Sync on app side**: On course outline screen, check if the course content is up to date and update it if necessary.
    * **Sync user responses**: When the device is offline, save user responses locally and send them to the server when the device is online.
* **Selective Download**: Allow users to choose specific content to download for offline use.
* **Full Course Download**: Provide an option to download entire courses for offline access.

Supported xBlocks in offline mode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

It was decided to include a fraction of Open edX xBlocks to be supported.
The following list of blocks is currently planned to be added to the support:

* **Common problems**:
    * **Checkboxes** - full support
    * **Dropdown** - full support
    * **Multiple Choice** - full support
    * **Numerical Input** - full support
    * **Text Input** - full support
    * **Checkboxes with Hints and Feedback** - partial support without Hints and Feedback
    * **Dropdown with Hints and Feedback** - partial support without Hints and Feedback
    * **Multiple Choice with Hints and Feedback** - partial support without Hints and Feedback
    * **Numerical Input with Hints and Feedback** - partially supported without Hints and Feedback
    * **Text Input with Hints and Feedback** - partially supported without Hints and Feedback
    * **Blank Advanced Problems** - partially supported, without loncapa/python problems or multi-part problems
* **Text**:
    * **Text** - full support
    * **IFrame Tool** - full support
    * **Raw HTML** - full support
    * **Zooming Image Tool** - full support
* **Video** - already supported


Consequences
------------

* Enhanced learner experience with flexible access to course materials.
* Increased accessibility for learners in regions with poor internet connectivity.
* Improved engagement and completion rates due to uninterrupted access to content.
* Simplified Maintenance by using a unified rendering view (`offline_view`), the complexity of maintaining separate renderers for online and offline content is significantly reduced.
* The proposed approach not only caters to the current needs of mobile users but also sets a foundation for expanding offline access to other platforms and uses.
* Potential increase in app size due to locally stored content.
* Increased complexity in managing content synchronization and updates.
* Need for continuous monitoring and updates to handle new content types and formats.

Rejected Solutions
------------------

* **Store common .js and .css files of blocks in a separate folder:**
    * This solution was rejected because it is unclear how to track potential changes to these files and re-generate the content of the blocks.

* **Generate content on the fly when the user requests it:**
    * This solution was rejected because it would require a significant amount of processing power and time to generate content for each block when requested.

* **Separate Offline Renderer**:
    * The initial proposal of creating a separate renderer for offline content was rejected due to the increased complexity and potential for inconsistent behavior between online and offline content.
