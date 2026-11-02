2. Offline Mode enhancements
=========================

Status
------

Proposed

Context
-------

`offline_view` generalized and can be used for Non-mobile offline mode, Anonymous access or Regular student access.
Static files like JavaScript and CSS will be de-duplicated based on their content hash.

Decisions
--------

1. Efficient resource management
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

 - Shared resources like JS and CSS files will be de-duplicated based on their content hash, to prevent duplication for every block.
 - All shared content should be stored in the separate ZIP archive.
 - This archive will be regenerated 1 time and contains all JS and CSS files related to default Xblocks.
 - Xblock specific resources will still be stored in the block ZIP archive.
 - This will ensure that the same resource is not duplicated across different blocks, reducing storage and bandwidth usage.


2. Anonymous access
~~~~~~~~~~~~~~~~~~~

 - Re-implement `public_view` on top of `offline_view`. If it is possible to get pre-rendered block without knowing user state, then it is possible to serve that pre-renderable view as the public experience for logged-out users.
 - This will allow broader access to educational content without the need for user authentication, potentially increasing user engagement and content reach.


3. Non-mobile offline mode
~~~~~~~~~~~~~~~~~~~~~~~~~~

 - The `offline_view` will be generalized to support non-mobile offline mode.
 - This mode will enable users on desktop and other non-mobile platforms to download and access course content without an active internet connection, providing greater flexibility in how content is accessed.


4. Regular student access
~~~~~~~~~~~~~~~~~~~~~~~~~

 - `student_view` will be implemented on top of `offline_view` wherever it is supported.
 - For XBlocks compatible with this architecture, offline-ready content will be served by default, and dynamic online features will be engaged only when a user has a reliable connection.
 - This setting is intended to improve the learning process by providing constant access to content when the Internet connection is unstable.


Consequences
------------

* **Resource Efficiency**: The avoidance of duplicating static resources for each block enhances the efficient use of storage and bandwidth.
* **Enhanced Flexibility**: The system can skip rendering blocks that require student-specific interactions, ensuring reliability and reducing the potential for behavior discrepancies between online and offline modes.
* **Broader Accessibility**: The ability to serve pre-rendered views to anonymous users increases the reach of educational content, making it more accessible to a wider audience.
