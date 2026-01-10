// MongoDB Initialization Script
// This runs on first container startup to create collections and indexes

// Switch to the pipeline database
db = db.getSiblingDB('pdf_pipeline');

// Create collections with schema validation
db.createCollection('conversion_dashboard', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['job_id', 'filename', 'status', 'created_at'],
      properties: {
        job_id: {
          bsonType: 'string',
          description: 'Unique job identifier'
        },
        filename: {
          bsonType: 'string',
          description: 'Original PDF filename'
        },
        status: {
          bsonType: 'string',
          enum: ['pending', 'processing', 'extracting', 'converting',
                 'ready_for_review', 'editing', 'finalizing',
                 'completed', 'failed', 'cancelled'],
          description: 'Conversion status'
        },
        created_at: {
          bsonType: 'date',
          description: 'Job creation timestamp'
        },
        completed_at: {
          bsonType: ['date', 'null'],
          description: 'Job completion timestamp'
        },
        publisher: {
          bsonType: ['string', 'null'],
          description: 'Publisher name'
        },
        book_title: {
          bsonType: ['string', 'null'],
          description: 'Book title'
        },
        pages: {
          bsonType: ['int', 'null'],
          description: 'Number of pages'
        },
        images: {
          bsonType: ['int', 'null'],
          description: 'Number of images extracted'
        },
        duration_seconds: {
          bsonType: ['double', 'null'],
          description: 'Processing duration'
        },
        error: {
          bsonType: ['string', 'null'],
          description: 'Error message if failed'
        }
      }
    }
  }
});

// Create indexes for common queries
db.conversion_dashboard.createIndex({ job_id: 1 }, { unique: true });
db.conversion_dashboard.createIndex({ status: 1 });
db.conversion_dashboard.createIndex({ created_at: -1 });
db.conversion_dashboard.createIndex({ publisher: 1 });
db.conversion_dashboard.createIndex({ 'created_at': 1, 'status': 1 });

// Create a TTL index to auto-delete old records (optional, 90 days)
// Uncomment to enable automatic cleanup:
// db.conversion_dashboard.createIndex(
//   { created_at: 1 },
//   { expireAfterSeconds: 90 * 24 * 60 * 60 }
// );

print('MongoDB initialization complete: pdf_pipeline database ready');
